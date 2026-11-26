"""Compile a screenplay into a timed, staged, camera-solved plan.

    plan = compile_plan(screenplay_dict, timing_dict_or_None)

Timing comes from the recorded/synthesised takes (voice/timing.json). Without
it, line lengths are estimated from word count so boards can be cut before
casting; the plan then reports `scratch_timing = True`.
"""

import json
import math
import re
from pathlib import Path

from . import mathx as mx
from . import sets as setlib
from .acting import Performer, Stage, GESTURES, EXPRESSIONS
from .camera import Solver
from .geom import Nav, element_obb

GREEN_ROOM = (-400.0, -400.0)
SET_SPACING = 70.0


class CompileError(ValueError):
    pass


class Plan:
    def __init__(self, sp):
        self.sp = sp
        self.fps = sp.get("fps", 24)
        self.sets = {}
        self.stage = Stage()
        self.shots = []
        self.lines = []
        self.fx = []
        self.music = []
        self.duration = 0.0
        self.scratch_timing = False
        self.notes = []
        self._navs = {}

    # ------------------------------------------------------------ resolution
    def point(self, name, set_, t):
        if isinstance(name, (list, tuple)):
            if len(name) == 2:
                return set_.world((name[0], name[1], 1.0))
            return set_.world(tuple(name))
        if "." in name:
            cid, anchor = name.split(".", 1)
            if cid in self.stage.performers:
                return self.stage.performers[cid].anchor(anchor, t)
        if name in self.stage.performers:
            return self.stage.performers[name].anchor("eyes", t)
        if name in set_.points:
            return set_.world(set_.points[name])
        if name in set_.marks:
            m = set_.marks[name]
            return set_.world((m[0], m[1], 1.1))
        if name in self.stage.points:
            return self.stage.points[name]
        raise CompileError(f"Unknown point '{name}' in set {set_.id}")

    def nav(self, set_, radius):
        key = (set_.id, round(radius, 2))
        if key not in self._navs:
            boxes = [element_obb(e, set_.offset) for e in set_.elements if e.get("nav")]
            self._navs[key] = Nav(set_, [b for b in boxes if b is not None], radius=radius)
        return self._navs[key]

    def frame(self, t):
        return int(round(t * self.fps)) + 1

    def summary(self):
        perf = self.stage.performers
        events = []
        for cid, p in perf.items():
            for (t, kind, data) in p.events:
                where = p.where(t)
                if where is None:
                    continue
                shot = self.shot_at(t)
                events.append(dict(t=round(t, 4), kind=kind, who=cid, surface=self.sets[where].surface if where in self.sets else "wood",
                                   set=where, shot=shot["id"] if shot else None, scale=p.body.k))
        events.sort(key=lambda e: e["t"])
        return dict(
            title=self.sp.get("title"), narrator=self.sp.get("narrator"), fps=self.fps, duration=round(self.duration, 4),
            frames=self.frame(self.duration) - 1, scratch_timing=self.scratch_timing,
            shots=[dict(id=s["id"], scene=s["scene"], set=s["set"], time=s["time"], beat=s.get("beat"),
                        t0=round(s["t0"], 4), t1=round(s["t1"], 4), f0=self.frame(s["t0"]), f1=self.frame(s["t1"]) - 1,
                        size=s["cam"].get("size"), ambience=s.get("ambience"), reverb=list(self.sets[s["set"]].reverb), subjects=s["camera"]["subjects"] if s.get("camera") else [])
                   for s in self.shots],
            lines=self.lines, events=events, fx=self.fx, music=self.music, notes=self.notes,
        )

    def shot_at(self, t):
        for s in self.shots:
            if s["t0"] <= t < s["t1"]:
                return s
        return self.shots[-1] if self.shots else None


def _line_lookup(sp):
    return {l["id"]: l for l in sp.get("lines", [])}


def _estimate(text):
    words = len(re.findall(r"[\w']+", text))
    # Fitted on 35 real Qwen3/Kokoro takes (One Hug Left, Three Words): mean error 0.37 s.
    return 0.26 + 0.30 * words


def compile_plan(sp, timing=None, solve_cameras=True):
    plan = Plan(sp)
    lines_by_id = _line_lookup(sp)
    tl = (timing or {}).get("lines", {})
    if not timing:
        plan.scratch_timing = True

    # Sets, in first-use order.
    for sc in sp["scenes"]:
        sid = sc["set"]
        if sid not in plan.sets:
            s = setlib.load(sid)
            s.offset = (len(plan.sets) * SET_SPACING, 0.0, 0.0)
            plan.sets[sid] = s

    # Cast
    for i, (cid, c) in enumerate(sp["cast"].items()):
        p = Performer(cid, c.get("scale", 1.0), c.get("build", 1.0), seed=17 + i * 31)
        plan.stage.add(p)
        p.place(0.0, (GREEN_ROOM[0] + i * 4, GREEN_ROOM[1]), 0.0, where=None)

    t = 0.0
    voice_cursor = 0.0
    for sc in sp["scenes"]:
        set_ = plan.sets[sc["set"]]
        time_of_day = sc.get("time", set_.default_time)
        scene_t0 = t
        cast = sc.get("cast", {})
        for cid, p in plan.stage.performers.items():
            p.outfits.append((scene_t0, sc.get("wardrobe", {}).get(cid, "default")))
            if cid in cast:
                _place(plan, p, set_, cast[cid], scene_t0)
            else:
                i = list(plan.stage.performers).index(cid)
                p.place(scene_t0, (GREEN_ROOM[0] + i * 4, GREEN_ROOM[1]), 0.0, where=None)
        for k, v in sc.get("start_expr", {}).items():
            plan.stage.performers[k].face(scene_t0, v)
        for k, v in sc.get("start_look", {}).items():
            plan.stage.performers[k].look(scene_t0, _look_target(plan, set_, v, scene_t0))
        for k, v in sc.get("start_badge", {}).items():
            plan.stage.performers[k].badge(scene_t0, v)

        for sh in sc["shots"]:
            shot = dict(id=sh["id"], scene=sc["id"], set=sc["set"], time=time_of_day, t0=t, beat=sh.get("beat", sc.get("beat")),
                        cam=sh.get("cam", {}), ambience=sh.get("ambience", sc.get("ambience")), spec=sh)
            # ---- place lines
            line_times = {}
            offset = t + sh.get("lead", 0.12 if t > 0 else 0.25)
            offset = max(offset, voice_cursor + 0.10)
            end_need = t
            for entry in sh.get("lines", []):
                if isinstance(entry, str):
                    entry = {"id": entry}
                lid = entry["id"]
                if lid not in lines_by_id:
                    raise CompileError(f"{sh['id']}: unknown line '{lid}'")
                L = lines_by_id[lid]
                info = tl.get(lid)
                dur = info["dur"] if info else _estimate(L["text"])
                start = t + entry["at"] if "at" in entry else offset
                end = start + dur
                line_times[lid] = (start, end)
                plan.lines.append(dict(id=lid, who=L["who"], kind=L.get("kind", "vo"), text=L["text"], t0=round(start, 4),
                                       t1=round(end, 4), shot=sh["id"], file=info.get("file") if info else None,
                                       words=[dict(w=w["w"], s=round(start + w["s"], 4), e=round(start + w["e"], 4)) for w in info.get("words", [])] if info else None,
                                       gain=L.get("gain", 0.0)))
                if L.get("kind") == "dialogue" and info and info.get("env"):
                    plan.stage.performers[L["who"]].talk(start, info["env"], info.get("env_fps", 24))
                elif L.get("kind") == "dialogue":
                    n = int(dur * 24)
                    env = [0.35 + 0.3 * math.sin(i * 1.3) ** 2 for i in range(n)]
                    plan.stage.performers[L["who"]].talk(start, env, 24)
                offset = end + entry.get("gap", 0.14)
                voice_cursor = max(voice_cursor, end)
                if not entry.get("spill"):
                    end_need = max(end_need, end)
            dur = sh.get("dur")
            if dur is None:
                dur = max(sh.get("min", 1.3), end_need - t + sh.get("pad", 0.3)) if sh.get("lines") else 2.0
            dur = max(dur, end_need - t + 0.05) if sh.get("lines") and not sh.get("dur_strict") else dur
            shot["t1"] = t + dur
            shot["lines"] = line_times
            # ---- actions
            last_end = {}
            for a in sh.get("act", []):
                _action(plan, set_, shot, a, line_times, last_end)
            plan.shots.append(shot)
            t += dur
        plan.notes.append(f"scene {sc['id']} at {sc['set']} {time_of_day}: {scene_t0:.2f}-{t:.2f}s")
    plan.duration = t

    # Music cues to absolute times
    for m in sp.get("music", []):
        sh = next((s for s in plan.shots if s["id"] == m["at"]), None)
        if sh is None:
            raise CompileError(f"music cue at unknown shot {m['at']}")
        plan.music.append(dict(m, t=round(sh["t0"] + m.get("offset", 0.0), 4)))

    if solve_cameras:
        solver = Solver(plan)
        for shot in plan.shots:
            shot["camera"] = solver.solve(shot)
            # Expose the camera position for "look at camera" acting.
            plan.stage.points["__cam__" + shot["id"]] = shot["camera"]["frames"][0][1]
    return plan


def _seat_world(set_, name):
    st = set_.seats[name]
    x, y = set_.world((st["x"], st["y"]))
    return dict(x=x, y=y, z=st["z"] + set_.offset[2], yaw=math.radians(st["yaw"]), kind=st.get("kind", "chair"), name=name)


def _place(plan, p, set_, spec, t):
    if isinstance(spec, str):
        spec = {"seat": spec} if spec in set_.seats else {"mark": spec}
    if "seat" in spec:
        seat = _seat_world(set_, spec["seat"])
        p.place(t, (seat["x"], seat["y"]), seat["yaw"], floor=set_.offset[2],
                posture="bed" if seat["kind"] == "bed" else "sit", seat=seat, where=set_.id)
        return
    if "contact" in spec:
        other = plan.stage.performers[spec["contact"]]
        ox, oy = other.state["pos"]
        oyaw = other.state["yaw"]
        gap = _contact_gap(p, other)
        x, y = ox + math.sin(oyaw) * gap, oy - math.cos(oyaw) * gap
        p.place(t, (x, y), mx.yaw_to((x, y), (ox, oy)), floor=set_.offset[2], where=set_.id)
        return
    if "mark" in spec:
        if spec["mark"] not in set_.marks:
            raise CompileError(f"{p.id}: unknown mark '{spec['mark']}' in {set_.id}")
        x, y, yaw = set_.marks[spec["mark"]]
    else:
        x, y = spec["at"]
        yaw = spec.get("yaw", 0.0)
    if "yaw" in spec:
        yaw = spec["yaw"]
    wx, wy = set_.world((x, y))
    p.place(t, (wx, wy), math.radians(yaw), floor=set_.offset[2], where=set_.id)


def _look_target(plan, set_, v, t, shot_id=None):
    if v in (None, "none", "ahead"):
        return None
    if v == "cam":
        return "__cam__" + (shot_id or "")
    if isinstance(v, str) and v.startswith("cam:"):
        return "__cam__" + v[4:]
    if isinstance(v, str) and v in plan.stage.performers:
        return v
    return plan.point(v, set_, t)


def _time(spec, shot, line_times, last_end, who):
    if spec is None:
        return last_end.get(who, shot["t0"])
    if isinstance(spec, (int, float)):
        return shot["t0"] + spec
    m = re.fullmatch(r"(line|end):([\w-]+)([+-][\d.]+)?", spec)
    if m:
        kind, lid, off = m.groups()
        if lid not in line_times:
            raise CompileError(f"{shot['id']}: action timed to line '{lid}' which is not in this shot")
        base = line_times[lid][0 if kind == "line" else 1]
        return base + float(off or 0)
    m = re.fullmatch(r"\+([\d.]+)", spec)
    if m:
        return last_end.get(who, shot["t0"]) + float(m.group(1))
    raise CompileError(f"{shot['id']}: bad time '{spec}'")


def _yaw_toward(plan, set_, p, target, t):
    st = p.state
    if isinstance(target, (int, float)):
        return math.radians(target)
    if isinstance(target, str) and target in plan.stage.performers:
        o = plan.stage.performers[target].state["pos"]
    elif isinstance(target, str) and target in set_.marks:
        m = set_.marks[target]
        o = set_.world((m[0], m[1]))
    else:
        o = plan.point(target, set_, t)
    return mx.yaw_to(st["pos"], o)


def _contact_gap(a, b):
    """Root distance for a front-to-front embrace.

    The shorter actor's head (radius 0.65 studs) protrudes past their torso and meets the
    taller actor's chest, which also tilts forward in the hug; size the gap for that.
    """
    from .rig import HEAD_RADIUS
    short, tall = (a, b) if a.body.height < b.body.height else (b, a)
    head = HEAD_RADIUS * short.body.s
    gap = max(a.body.half_depth + b.body.half_depth, head + tall.body.half_depth)
    if tall.body.height - short.body.height > 0.15:
        gap += 0.07
    return gap + 0.03


def _action(plan, set_, shot, a, line_times, last_end):
    who_list = a["who"] if isinstance(a["who"], list) else [a["who"]]
    for who in who_list:
        if who not in plan.stage.performers:
            raise CompileError(f"{shot['id']}: unknown performer '{who}'")
        p = plan.stage.performers[who]
        if p.state.get("where") != set_.id:
            raise CompileError(f"{shot['id']}: {who} acts but is not cast in this scene")
        t = _time(a.get("t"), shot, line_times, last_end, who)
        do = a.get("do")
        end = t
        if "expr" in a:
            p.face(t + a.get("expr_delay", 0.0), a["expr"])
        if "look" in a and do != "look":
            p.look(t, _look_target(plan, set_, a["look"], t, shot["id"]))
        if do in (None, "face", "expr"):
            pass
        elif do in ("walk", "run", "approach"):
            run = do == "run" or a.get("run", False)
            target = a["to"]
            radius = 2.05 * p.body.s * p.body.build + 0.06
            stop_face = None
            if isinstance(target, str) and target in plan.stage.performers:
                other = plan.stage.performers[target]
                op = other.state["pos"]
                gap = a.get("gap", _contact_gap(p, other) if do == "approach" else 0.9)
                d = mx.v_norm((p.state["pos"][0] - op[0], p.state["pos"][1] - op[1], 0))
                if "side" in a:
                    ang = other.state["yaw"] + math.radians(a["side"])
                    d = (math.sin(ang), -math.cos(ang), 0)
                dest = (op[0] + d[0] * gap, op[1] + d[1] * gap)
                if "offset" in a:
                    dest = (dest[0] - d[1] * a["offset"], dest[1] + d[0] * a["offset"])
                stop_face = mx.yaw_to(dest, op)
            elif isinstance(target, str) and target in set_.marks:
                m = set_.marks[target]
                dest = set_.world((m[0], m[1]))
                stop_face = math.radians(m[2])
            elif isinstance(target, str) and target in set_.seats:
                st = _seat_world(set_, target)
                dest = (st["x"] + math.sin(st["yaw"]) * 0.45 * p.body.k, st["y"] - math.cos(st["yaw"]) * 0.45 * p.body.k)
                stop_face = st["yaw"]
            else:
                dest = set_.world(tuple(target))
            if "face_offset" in a and stop_face is not None:
                stop_face += math.radians(a["face_offset"])
            if "face" in a:
                stop_face = _yaw_toward(plan, set_, p, a["face"], t) if not isinstance(a["face"], (int, float)) else math.radians(a["face"])
                # recompute from destination
                if isinstance(a["face"], str) and a["face"] in plan.stage.performers:
                    stop_face = mx.yaw_to(dest, plan.stage.performers[a["face"]].state["pos"])
            if "face_offset" in a and "face" in a:
                stop_face += math.radians(a["face_offset"])
            path = plan.nav(set_, radius).route(p.state["pos"], dest) if not a.get("direct") else [dest]
            if a.get("backward") and stop_face is None:
                stop_face = p.state["yaw"]
            end = p.walk(t, path, speed=a.get("speed", 1.15), run=run, end_yaw=stop_face, duration=a.get("dur"), backward=a.get("backward", False))
        elif do == "turn":
            yaw = _yaw_toward(plan, set_, p, a["to"], t)
            end = p.turn(t, yaw, a.get("dur", 0.55))
        elif do == "sit":
            seat = _seat_world(set_, a["seat"])
            ap = (seat["x"] + math.sin(seat["yaw"]) * 0.45 * p.body.k, seat["y"] - math.cos(seat["yaw"]) * 0.45 * p.body.k)
            if math.hypot(ap[0] - p.state["pos"][0], ap[1] - p.state["pos"][1]) > 0.15:
                path = plan.nav(set_, 1.9 * p.body.s * p.body.build).route(p.state["pos"], ap)
                t = p.walk(t, path, end_yaw=seat["yaw"])
            elif abs(mx.wrap(p.state["yaw"] - seat["yaw"])) > 0.3:
                t = p.turn(t, seat["yaw"])
            end = p.sit(t, seat, a.get("dur", 0.7))
        elif do == "stand":
            to = set_.world(tuple(a["to"])) if "to" in a else None
            end = p.stand_up(t, to, a.get("dur", 0.7))
        elif do in ("crouch", "uncrouch"):
            end = p.crouch(t, do == "crouch", a.get("dur", 0.45))
        elif do == "look":
            p.look(t, _look_target(plan, set_, a.get("at"), t, shot["id"]))
            end = t
        elif do == "badge":
            p.badge(t, a["text"])
            if a.get("sfx", True):
                p.events.append((t, "badge", {"text": a["text"]}))
        elif do == "fx":
            plan.fx.append(dict(kind=a["kind"], t=round(t, 4), dur=a.get("dur", 0.8), at=a.get("at", who + ".chest"), who=who, set=set_.id,
                                color=a.get("color")))
            end = t
        elif do == "release":
            names = a.get("gesture")
            for i, (g0, g1, gname, gp) in enumerate(p.gestures):
                if g0 <= t < g1 and (names is None or gname == names):
                    p.gestures[i] = (g0, t, gname, gp)
                    p.allow = [(a0, (t if (a0 == g0 and a1 == g1) else a1), m, o, d) for (a0, a1, m, o, d) in p.allow]
            end = t
        elif do == "sfx":
            p.events.append((t, a["kind"], {}))
        elif do in GESTURES:
            dur = a.get("dur", 1.2)
            params = {k: v for k, v in a.items() if k not in ("who", "do", "t", "dur", "expr", "look", "expr_delay", "hold")}
            if "with" in params:
                params["who"] = params.pop("with")
            # Targets that name set points, marks or coordinates become world points now;
            # actor targets ("mom", "mom.head") stay symbolic so they follow the actor.
            for key in ("at", "to"):
                v = params.get(key)
                if key == "at" and do == "hug":
                    continue
                if isinstance(v, (list, tuple)):
                    params[key] = plan.point(list(v), set_, t)
                elif isinstance(v, str) and v.split(".", 1)[0] not in plan.stage.performers:
                    params[key] = plan.point(v, set_, t)
            p.gesture(t, t + dur, do, **params)
            if do == "hug":
                p.events.append((t + 0.3, "cloth", {}))
            end = t + dur if not a.get("hold") else t
        else:
            raise CompileError(f"{shot['id']}: unknown action '{do}'. Gestures: {sorted(GESTURES)}")
        last_end[who] = end


def load_screenplay(path):
    return json.loads(Path(path).read_text())


def load_timing(episode_dir):
    p = Path(episode_dir) / "voice" / "timing.json"
    if p.exists():
        return json.loads(p.read_text())
    return None
