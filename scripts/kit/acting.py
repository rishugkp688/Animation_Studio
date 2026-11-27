"""Character performance: locomotion, posture, gestures, gaze, face and lip-sync.

Pure Python. A Performer holds absolute-time timelines that the layout compiler
fills from the screenplay. `pose(t)` returns the full rig pose plus face channels
for one instant; the Blender builder bakes it and QC measures it, so what is
checked is exactly what renders.

Locomotion is solved from world-space footprints: a stance foot is a fixed world
point and the rigid R6 leg is aimed at it, with the hip height derived from the
leg length (an inverted pendulum). Stance feet therefore cannot slide.
"""

import bisect
import math

from . import mathx as mx
from .rig import Body, joint_matrices, points, aim_euler, to_local

FPS = 24

# Face atlases (index -> name). Faces are drawn in kit/faces.py with the same order.
EYES = ["open", "happy", "closed", "closed_happy", "closed_sad", "sad", "sad_wet", "wide",
        "worried", "angry", "determined", "soft", "half", "grumpy", "shut_tight", "look_up"]
MOUTHS = ["flat", "smile", "grin", "frown", "wobble", "o_small", "o_big", "talk_a",
          "talk_e", "talk_o", "smirk", "grit", "soft_smile", "flat_small", "frown_small", "shout"]
EXTRAS = ["none", "tears", "tear_drop", "blush"]
CLOSED_OF = {"sad": "closed_sad", "sad_wet": "closed_sad", "worried": "closed_sad", "wide": "closed",
             "happy": "closed_happy", "soft": "closed", "look_up": "closed"}

EXPRESSIONS = {
    "neutral": ("open", "flat", "none"),
    "calm": ("soft", "flat_small", "none"),
    "happy": ("happy", "smile", "none"),
    "joy": ("closed_happy", "grin", "none"),
    "tender": ("soft", "soft_smile", "none"),
    "sad": ("sad", "frown_small", "none"),
    "hurt": ("sad_wet", "wobble", "none"),
    "teary": ("sad_wet", "wobble", "tear_drop"),
    "cry": ("closed_sad", "wobble", "tears"),
    "sob": ("shut_tight", "shout", "tears"),
    "cry_smile": ("closed_happy", "soft_smile", "tears"),
    "teary_smile": ("sad_wet", "soft_smile", "tear_drop"),
    "shock": ("wide", "o_big", "none"),
    "surprise": ("wide", "o_small", "none"),
    "worried": ("worried", "flat_small", "none"),
    "scared": ("wide", "grit", "none"),
    "angry": ("angry", "grit", "none"),
    "grumpy": ("grumpy", "frown_small", "none"),
    "determined": ("determined", "flat", "none"),
    "sleepy": ("half", "flat_small", "none"),
    "smirk": ("half", "smirk", "none"),
    "shy": ("soft", "soft_smile", "blush"),
    "hopeful": ("look_up", "soft_smile", "none"),
    "eyes_closed": ("closed", "flat_small", "none"),
    "peaceful": ("closed", "soft_smile", "none"),
}


def face_code(expr):
    e, m, x = EXPRESSIONS[expr]
    return EYES.index(e), MOUTHS.index(m), EXTRAS.index(x)


class Segment:
    __slots__ = ("kind", "t0", "t1", "data")

    def __init__(self, kind, t0, t1, **data):
        self.kind, self.t0, self.t1, self.data = kind, t0, t1, data


def _path_build(pts):
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum


def _path_at(pts, cum, s):
    s = mx.clamp(s, 0.0, cum[-1])
    i = max(0, min(len(pts) - 2, bisect.bisect_right(cum, s) - 1))
    seg = cum[i + 1] - cum[i] or 1e-9
    u = (s - cum[i]) / seg
    a, b = pts[i], pts[i + 1]
    pos = (mx.lerp(a[0], b[0], u), mx.lerp(a[1], b[1], u))
    d = (b[0] - a[0], b[1] - a[1])
    n = math.hypot(*d) or 1.0
    return pos, (d[0] / n, d[1] / n)


def _heading_at(pts, cum, s, look=0.35):
    """Smoothed path heading (yaw) using a short look-ahead/behind window."""
    a, _ = _path_at(pts, cum, s - look)
    b, _ = _path_at(pts, cum, s + look)
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6:
        _, d = _path_at(pts, cum, s)
        return math.atan2(d[0], -d[1])
    return math.atan2(b[0] - a[0], -(b[1] - a[1]))


class Walk:
    """Footprint plan for one walk/run segment along a path."""

    def __init__(self, body, pts, t0, t1, start_feet, start_yaw, run=False, end_yaw=None, backward=False):
        self.body, self.pts, self.t0, self.t1, self.run = body, pts, t0, t1, run
        self.backward = backward
        self.cum = _path_build(pts)
        self.L = self.cum[-1]
        self.start_yaw = start_yaw
        self.end_yaw = end_yaw
        T = max(t1 - t0, 1e-3)
        self.ramp = min(0.28 if run else 0.38, T / 3)
        self.vmax = self.L / max(T - self.ramp, 1e-3)
        stride = body.leg * (1.15 if run else 0.72)
        self.n = max(1, round(self.L / stride)) if self.L > 0.05 else 0
        self.D = self.L / self.n if self.n else 0.0
        lat = body.lateral
        # Footprints: index -> (side, world xy, s_value). Side alternates starting with right.
        prints = []
        self.start_feet = start_feet
        for j in range(1, self.n + 2):
            side = "r" if j % 2 else "l"
            sj = min(j * self.D, self.L)
            pos, d = _path_at(pts, self.cum, sj)
            yaw = _heading_at(pts, self.cum, sj) + (math.pi if backward else 0.0) if j <= self.n else (end_yaw if end_yaw is not None else _heading_at(pts, self.cum, sj) + (math.pi if backward else 0.0))
            nx, ny = math.cos(yaw), math.sin(yaw)  # local +X (character's left) in world
            off = lat if side == "l" else -lat
            prints.append((side, (pos[0] + nx * off, pos[1] + ny * off), sj))
        self.prints = prints

    def speed_frac(self, t):
        """Current speed as a fraction of cruise speed (0 at rest, 1 when cruising)."""
        u = t - self.t0
        T = self.t1 - self.t0
        r = self.ramp
        if u <= 0 or u >= T:
            return 0.0
        if u < r:
            return u / r
        if u > T - r:
            return (T - u) / r
        return 1.0

    def s_at(self, t):
        u = t - self.t0
        T = self.t1 - self.t0
        r, v = self.ramp, self.vmax
        if u <= 0:
            return 0.0
        if u >= T:
            return self.L
        if u < r:
            return 0.5 * v / r * u * u
        if u > T - r:
            w = T - u
            return self.L - 0.5 * v / r * w * w
        return 0.5 * v * r + v * (u - r)

    def feet_at(self, t, floor):
        """World foot targets and which feet are planted."""
        s = self.s_at(t)
        D = self.D or 1.0
        feet, planted = {}, {}
        prev = {"r": self.start_feet["r"], "l": self.start_feet["l"]}
        for j, (side, pos, sj) in enumerate(self.prints, start=1):
            lift_s = max(0.0, (j - 1.5) * D)
            land_s = min(self.L, (j - 0.5) * D) if j <= self.n else self.L
            if j > self.n:
                lift_s = max(0.0, (self.n - 0.5) * D)
            if s < lift_s:
                break
            if s >= land_s - 1e-9:
                prev[side] = (pos[0], pos[1], floor)
                continue
            p = mx.smooth((s - lift_s) / max(land_s - lift_s, 1e-6))
            a = prev[side]
            lift = (0.07 if self.run else 0.035) * self.body.k * math.sin(math.pi * p)
            feet[side] = (mx.lerp(a[0], pos[0], p), mx.lerp(a[1], pos[1], p), floor + lift)
            planted[side] = False
        for side in ("r", "l"):
            if side not in feet:
                feet[side] = prev[side]
                planted[side] = True
        return s, feet, planted

    def yaw_at(self, t):
        s = self.s_at(t)
        if self.L < 0.05:
            return self.start_yaw
        h = _heading_at(self.pts, self.cum, s) + (math.pi if self.backward else 0.0)
        u = mx.smooth((t - self.t0) / 0.35)
        yaw = mx.angle_lerp(self.start_yaw, h, u)
        if self.end_yaw is not None:
            w = mx.smooth((t - (self.t1 - 0.35)) / 0.35)
            yaw = mx.angle_lerp(yaw, self.end_yaw, w)
        return yaw


class Performer:
    def __init__(self, cid, scale=1.0, build=1.0, seed=0):
        self.id = cid
        self.body = Body(scale, build)
        self.seed = seed or int(mx.hash01(cid) * 1000)
        self.segments = []      # locomotion and posture, chronological
        self.gestures = []      # (t0, t1, name, params)
        self.looks = []         # (t, target)
        self.faces = []         # (t, expr)
        self.talks = []         # (t0, t1, envelope, env_fps)
        self.badges = []        # (t, text)
        self.allow = []         # allowed contacts for QC: (t0, t1, my_part_glob, other_glob, max_depth)
        self.stage = None
        self.events = []        # foley events: (t, kind, data)
        self.outfits = []       # (t, outfit name)
        self._walks = {}
        self.state = None       # running end state while compiling

    # ------------------------------------------------------------ compile API
    def place(self, t, pos, yaw, floor=0.0, posture="stand", seat=None, where=None):
        lat = self.body.lateral
        nx, ny = math.cos(yaw), math.sin(yaw)
        feet = {"r": (pos[0] - nx * lat, pos[1] - ny * lat, floor), "l": (pos[0] + nx * lat, pos[1] + ny * lat, floor)}
        self.state = dict(pos=tuple(pos[:2]), yaw=yaw, floor=floor, posture=posture, seat=seat, feet=feet, where=where)
        self.segments.append(Segment("place", t, t, state=dict(self.state)))

    def walk(self, t0, path, speed=1.15, run=False, end_yaw=None, duration=None, backward=False):
        st = self.state
        pts = [st["pos"]] + [tuple(p[:2]) for p in path]
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        k = self.body.k
        if duration is None:
            v = speed * (2.6 if run else 1.0) * (0.82 + 0.18 * k)
            duration = L / v + (0.28 if run else 0.38)
        t1 = t0 + max(duration, 0.4)
        if backward:
            duration = duration or (L / (0.6 * speed) + 0.3)
            t1 = t0 + max(duration, 0.4)
        w = Walk(self.body, pts, t0, t1, st["feet"], st["yaw"], run, end_yaw, backward)
        seg = Segment("walk", t0, t1, walk=w, run=run)
        self.segments.append(seg)
        final_yaw = w.yaw_at(t1)
        _, feet, _ = w.feet_at(t1 + 1e-4, st["floor"])
        self.state = dict(st, pos=pts[-1], yaw=final_yaw, feet=feet)
        for j, (side, pos, sj) in enumerate(w.prints, start=1):
            land_s = min(w.L, (j - 0.5) * w.D) if j <= w.n else w.L
            # time when s reaches land_s
            tt = self._time_for_s(w, land_s)
            self.events.append((tt, "run_step" if run else "step", {"side": side}))
        return t1

    @staticmethod
    def _time_for_s(w, s):
        lo, hi = w.t0, w.t1
        for _ in range(40):
            mid = (lo + hi) / 2
            if w.s_at(mid) < s:
                lo = mid
            else:
                hi = mid
        return hi

    def turn(self, t0, yaw, duration=0.55):
        st = self.state
        t1 = t0 + duration
        self.segments.append(Segment("turn", t0, t1, a=st["yaw"], b=yaw, pos=st["pos"], feet=st["feet"], floor=st["floor"]))
        lat = self.body.lateral
        nx, ny = math.cos(yaw), math.sin(yaw)
        p = st["pos"]
        feet = {"r": (p[0] - nx * lat, p[1] - ny * lat, st["floor"]), "l": (p[0] + nx * lat, p[1] + ny * lat, st["floor"])}
        self.events.append((t0 + duration * 0.45, "shuffle", {}))
        self.state = dict(st, yaw=yaw, feet=feet)
        return t1

    def sit(self, t0, seat, duration=0.7):
        """seat: dict(x, y, z (seat top), yaw, kind='chair'|'bed')"""
        st = self.state
        t1 = t0 + duration
        self.segments.append(Segment("sit", t0, t1, a=dict(st), seat=seat))
        self.events.append((t0 + duration * 0.8, "sit", {}))
        self.state = dict(st, posture="bed" if seat.get("kind") == "bed" else "sit", seat=seat, yaw=seat["yaw"])
        return t1

    def stand_up(self, t0, to=None, duration=0.7):
        st = self.state
        seat = st["seat"]
        yaw = seat["yaw"]
        fx, fy = math.sin(yaw), -math.cos(yaw)
        dest = to or (seat["x"] + fx * 0.42 * self.body.k, seat["y"] + fy * 0.42 * self.body.k)
        t1 = t0 + duration
        self.segments.append(Segment("stand_up", t0, t1, seat=seat, dest=dest, floor=st["floor"]))
        lat = self.body.lateral
        nx, ny = math.cos(yaw), math.sin(yaw)
        feet = {"r": (dest[0] - nx * lat, dest[1] - ny * lat, st["floor"]), "l": (dest[0] + nx * lat, dest[1] + ny * lat, st["floor"])}
        self.state = dict(st, posture="stand", seat=None, pos=dest, feet=feet)
        return t1

    def crouch(self, t0, down=True, duration=0.45):
        self.segments.append(Segment("crouch" if down else "uncrouch", t0, t0 + duration))
        self.state = dict(self.state, posture="crouch" if down else "stand")
        return t0 + duration

    def gesture(self, t0, t1, name, **params):
        if name in ("hug", "ruffle", "hold_hand", "hold_shoulders") and "who" not in params:
            raise ValueError(f"{self.id}: '{name}' needs a partner, e.g. \"with\": \"mom\"")
        self.gestures.append((t0, t1, name, params))
        g = GESTURES.get(name)
        if g and g.contacts:
            for part, other, depth in g.contacts(params):
                self.allow.append((t0, t1, part, other, depth))

    def look(self, t, target):
        self.looks.append((t, target))
        self.looks.sort(key=lambda x: x[0])

    def face(self, t, expr):
        if expr not in EXPRESSIONS:
            raise ValueError(f"{self.id}: unknown expression '{expr}'. Use one of {sorted(EXPRESSIONS)}")
        self.faces.append((t, expr))
        self.faces.sort(key=lambda x: x[0])

    def talk(self, t0, envelope, env_fps):
        self.talks.append((t0, t0 + len(envelope) / env_fps, envelope, env_fps))

    def badge(self, t, text):
        self.badges.append((t, str(text)))
        self.badges.sort(key=lambda x: x[0])

    # ------------------------------------------------------------ evaluation
    def _segment_at(self, t):
        cur = None
        for seg in self.segments:
            if seg.t0 <= t + 1e-9:
                cur = seg
            else:
                break
        return cur

    def _state_before(self, seg):
        """End state of everything before `seg`; reconstructed by replaying."""
        return seg.data.get("a")

    def where(self, t):
        st = None
        for seg in self.segments:
            if seg.kind == "place" and seg.t0 <= t + 1e-9:
                st = seg.data["state"].get("where")
        return st

    def base(self, t):
        """Locomotion/posture layer: root, pelvis and leg targets. No gestures."""
        b = self.body
        # Replay segments to find the active one and the state it starts from.
        state = None
        active = None
        for seg in self.segments:
            if seg.t0 > t + 1e-9:
                break
            if seg.kind == "place":
                state = dict(seg.data["state"])
                active = None
                continue
            if t < seg.t1:
                active = seg
                break
            state = self._apply_end(state, seg)
            active = None
        if state is None:
            return None
        out = dict(root=(state["pos"][0], state["pos"][1], state["floor"]), yaw=state["yaw"], floor=state["floor"],
                   posture=state["posture"], feet=dict(state["feet"]), planted={"r": True, "l": True},
                   sit=0.0, crouch=0.0, seat=state.get("seat"), moving=0.0, run=False, swing=0.0, where=state.get("where"))
        if state["posture"] in ("sit", "bed"):
            out["sit"] = 1.0
        if state["posture"] == "crouch":
            out["crouch"] = 1.0
        if active is None:
            return out
        u = (t - active.t0) / max(active.t1 - active.t0, 1e-6)
        k = active.kind
        if k == "walk":
            w = active.data["walk"]
            s, feet, planted = w.feet_at(t, state["floor"])
            pos, _ = _path_at(w.pts, w.cum, s)
            out.update(root=(pos[0], pos[1], state["floor"]), yaw=w.yaw_at(t), feet=feet, planted=planted,
                       moving=1.0, run=active.data["run"], speed=w.speed_frac(t))
        elif k == "turn":
            d = active.data
            yaw = mx.angle_lerp(d["a"], d["b"], mx.smooth(u))
            lat = b.lateral
            p = d["pos"]

            def foot(side, y):
                nx, ny = math.cos(y), math.sin(y)
                o = lat if side == "l" else -lat
                return (p[0] + nx * o, p[1] + ny * o, d["floor"])
            feet, planted = {}, {}
            # Two quick steps: right foot moves in the first half, left in the second.
            for side, (lo, hi) in (("r", (0.0, 0.55)), ("l", (0.45, 1.0))):
                if u <= lo:
                    feet[side], planted[side] = d["feet"][side], True
                elif u >= hi:
                    feet[side], planted[side] = foot(side, d["b"]), True
                else:
                    q = mx.smooth((u - lo) / (hi - lo))
                    a, c = d["feet"][side], foot(side, d["b"])
                    feet[side] = (mx.lerp(a[0], c[0], q), mx.lerp(a[1], c[1], q), d["floor"] + 0.03 * b.k * math.sin(math.pi * q))
                    planted[side] = False
            out.update(yaw=yaw, feet=feet, planted=planted, moving=0.3)
        elif k == "sit":
            seat = active.data["seat"]
            a = active.data["a"]
            q = mx.ease_out(min(u / 0.7, 1.0))
            out.update(root=(mx.lerp(a["pos"][0], seat["x"], q), mx.lerp(a["pos"][1], seat["y"], q), state["floor"]),
                       yaw=mx.angle_lerp(a["yaw"], seat["yaw"], mx.smooth(u * 1.6)), sit=mx.smooth((u - 0.15) / 0.85), seat=seat)
        elif k == "stand_up":
            seat, dest = active.data["seat"], active.data["dest"]
            q = mx.ease_out(min(u / 0.6, 1.0))
            y = seat["yaw"]
            lat = b.lateral
            nx, ny = math.cos(y), math.sin(y)
            feet = {"r": (dest[0] - nx * lat, dest[1] - ny * lat, state["floor"]), "l": (dest[0] + nx * lat, dest[1] + ny * lat, state["floor"])}
            out.update(root=(mx.lerp(seat["x"], dest[0], q), mx.lerp(seat["y"], dest[1], q), state["floor"]),
                       yaw=y, sit=1 - mx.smooth((u - 0.35) / 0.65), seat=seat, feet=feet)
        elif k == "crouch":
            out["crouch"] = mx.smooth(u)
        elif k == "uncrouch":
            out["crouch"] = 1 - mx.smooth(u)
        return out

    def _apply_end(self, state, seg):
        k = seg.kind
        st = dict(state)
        if k == "walk":
            w = seg.data["walk"]
            _, feet, _ = w.feet_at(seg.t1 + 1e-4, st["floor"])
            st.update(pos=w.pts[-1], yaw=w.yaw_at(seg.t1), feet=feet)
        elif k == "turn":
            lat = self.body.lateral
            y = seg.data["b"]
            p = seg.data["pos"]
            nx, ny = math.cos(y), math.sin(y)
            st.update(yaw=y, feet={"r": (p[0] - nx * lat, p[1] - ny * lat, st["floor"]),
                                   "l": (p[0] + nx * lat, p[1] + ny * lat, st["floor"])})
        elif k == "sit":
            seat = seg.data["seat"]
            st.update(posture="bed" if seat.get("kind") == "bed" else "sit", seat=seat, yaw=seat["yaw"], pos=(seat["x"], seat["y"]))
        elif k == "stand_up":
            d = seg.data["dest"]
            y = seg.data["seat"]["yaw"]
            lat = self.body.lateral
            nx, ny = math.cos(y), math.sin(y)
            st.update(posture="stand", seat=None, pos=d, yaw=y,
                      feet={"r": (d[0] - nx * lat, d[1] - ny * lat, st["floor"]), "l": (d[0] + nx * lat, d[1] + ny * lat, st["floor"])})
        elif k == "crouch":
            st["posture"] = "crouch"
        elif k == "uncrouch":
            st["posture"] = "stand"
        return st

    def anchor(self, name, t):
        """A world point from the base layer only (no gestures or gaze), safe for other actors to target."""
        key = round(t, 4)
        cache = self.__dict__.setdefault("_anchor_cache", {})
        if key not in cache:
            if len(cache) > 20000:
                cache.clear()
            p = self.pose(t, gestures=False, look=False)
            cache[key] = None if p is None else p["points"]
        pts = cache[key]
        return None if pts is None else pts[name]

    def pose(self, t, gestures=True, look=True):
        bs = self.base(t)
        if bs is None:
            return None
        b = self.body
        s = b.s
        x, y, z = bs["root"]
        yaw = bs["yaw"]
        fx, fy = math.sin(yaw), -math.cos(yaw)   # forward
        nx, ny = math.cos(yaw), math.sin(yaw)    # character's left
        ph = self.seed * 0.37
        breath = math.sin(mx.TAU * t / 3.4 + ph)
        sway = mx.noise1(t * 0.35, self.seed) * (1 - bs["moving"])

        # ---- gesture weights and lean
        active = []
        if gestures:
            for (g0, g1, name, params) in self.gestures:
                if g0 <= t <= g1 + 0.5:
                    g = GESTURES[name]
                    w = envelope(t, g0, g1, g.blend_in, g.blend_out)
                    if w > 0:
                        active.append((g, w, t - g0, g1 - g0, params))
        lean = [0.0, 0.0]
        crouch_extra = 0.0
        for g, w, lt, dur, params in active:
            if g.lean:
                lx, ly = g.lean(lt, dur, params)
                lean[0] += lx * w
                lean[1] += ly * w
            if g.crouch:
                crouch_extra = max(crouch_extra, g.crouch(lt, dur, params) * w)

        # ---- pelvis placement
        stand_h = b.hip_height
        posture = bs["posture"]
        pelvis_rot = [lean[0], lean[1], 0.0]
        # Stance-derived hip height for standing/walking.
        def hip_world(side, h):
            o = b.lateral if side == "l" else -b.lateral
            return (x + nx * o, y + ny * o, z + h)
        h_stand = stand_h
        for side in ("r", "l"):
            if bs["planted"].get(side):
                f = bs["feet"][side]
                hp = hip_world(side, 0)
                d2 = (f[0] - hp[0]) ** 2 + (f[1] - hp[1]) ** 2
                h_stand = min(h_stand, math.sqrt(max(b.leg ** 2 - d2, (0.5 * b.leg) ** 2)) + (f[2] - z))
        h_stand += 0.0025 * b.k * breath * (1 - bs["moving"])
        if bs["run"]:
            h_stand += 0.0
        crouch = max(bs["crouch"], crouch_extra)
        h_crouch = b.leg * 0.58
        h = mx.lerp(h_stand, h_crouch, crouch)
        root = (x, y, z)
        pelvis = (sway * 0.012 * b.k, 0.0, h)
        sitw = bs["sit"]
        if sitw > 0 and bs["seat"]:
            seat = bs["seat"]
            seat_h = seat["z"] + 0.5 * s + 0.004 - z
            if seat.get("kind") == "bed":
                pelvis_rot[0] += -0.30 * sitw
            pelvis = (pelvis[0] * (1 - sitw), mx.lerp(0.0, 0.10 * s, sitw) * 0, mx.lerp(h, seat_h, mx.smooth(sitw)))
            pelvis_rot[0] += 0.0
        spd = bs.get("speed", 0.0)
        if bs["run"]:
            pelvis_rot[0] += 0.16 * spd
        elif bs["moving"] > 0.5:
            pelvis_rot[0] += 0.04 * spd
        pelvis_rot[2] += 0.05 * math.sin(self._swing_phase(bs, t)) * bs["moving"]

        pose = dict(root=root, yaw=yaw, lean=(0.0, 0.0), pelvis=pelvis, pelvis_rot=tuple(pelvis_rot))
        mats = joint_matrices(b, pose)
        pm = mats["pelvis"]

        # ---- legs
        def leg_toward(side, world_target):
            pivot = mats["leg_" + side]
            piv = (pivot[0][3], pivot[1][3], pivot[2][3])
            local_dir = to_local_dir(pm, mx.v_sub(world_target, piv))
            return aim_euler(local_dir)

        legs = {}
        for side in ("r", "l"):
            stand_rot = leg_toward(side, bs["feet"][side])
            if crouch > 0:
                sign = -1 if side == "r" else 1
                cr = (sign * 0.95 - lean[0], 0.0, 0.0)
                stand_rot = tuple(mx.lerp(a, c, crouch) for a, c in zip(stand_rot, cr))
            if sitw > 0:
                seat = bs["seat"]
                down = 0.0
                splay = 0.06 if side == "l" else -0.06
                sit_dir = (fx * math.cos(down) + nx * splay, fy * math.cos(down) + ny * splay, -math.sin(down))
                piv = mats["leg_" + side]
                sit_rot = aim_euler(to_local_dir(pm, sit_dir))
                stand_rot = tuple(mx.lerp(a, c, mx.smooth(sitw)) for a, c in zip(stand_rot, sit_rot))
            legs[side] = stand_rot
        pose["leg_r"], pose["leg_l"] = legs["r"], legs["l"]
        if sitw < 0.5:
            # Rigid block feet pivot on their corners: lift the hips by any corner that dips under the floor.
            from .rig import part_boxes as _boxes
            m2 = joint_matrices(b, pose)
            bx = _boxes(b, m2)
            low = min(bx["leg_r"].lowest(), bx["leg_l"].lowest())
            if low < z:
                px, py, pz = pose["pelvis"]
                pose["pelvis"] = (px, py, pz + (z - low))
                mats = joint_matrices(b, pose)
                pm = mats["pelvis"]
                legs = {sd: (leg_toward(sd, bs["feet"][sd]) if crouch == 0 else legs[sd]) for sd in ("r", "l")}
                pose["leg_r"], pose["leg_l"] = legs["r"], legs["l"]

        # ---- arms: rest + walk swing
        swing = 0.0
        if bs["moving"] > 0.5:
            swing = legs["l"][0] - legs["r"][0]
        amp = 1.25 if bs["run"] else 0.85
        rest_out = 0.07 + 0.012 * breath
        arm_r = [-(swing * 0.5 * amp) if bs["moving"] > 0.5 else 0.0, -rest_out, 0.0]
        arm_l = [(swing * 0.5 * amp) if bs["moving"] > 0.5 else 0.0, rest_out, 0.0]
        if bs["run"]:
            arm_r[0] -= 0.25 * spd
            arm_l[0] -= 0.25 * spd
        if sitw > 0:
            arm_r = [mx.lerp(arm_r[0], -0.62, sitw), mx.lerp(arm_r[1], -0.10, sitw), 0.0]
            arm_l = [mx.lerp(arm_l[0], -0.62, sitw), mx.lerp(arm_l[1], 0.10, sitw), 0.0]
        if crouch > 0:
            arm_r[0] = mx.lerp(arm_r[0], -0.75, crouch)
            arm_l[0] = mx.lerp(arm_l[0], -0.75, crouch)
        # Counter the pelvis lean so hanging arms stay roughly vertical.
        arm_r[0] -= pelvis_rot[0] * 0.8
        arm_l[0] -= pelvis_rot[0] * 0.8

        # ---- neck from look-at
        neck = [0.0, 0.0, 0.0]
        gaze = [0.0, 0.0]
        angles = self._look_angles(t, mats) if look else None
        if angles:
            neck[0], neck[2], gaze[0], gaze[1] = angles
        # idle head life
        neck[2] += 0.035 * mx.noise1(t * 0.5, self.seed + 3)
        neck[0] += 0.02 * mx.noise1(t * 0.4, self.seed + 5) + 0.01 * breath
        talk_amt = self._talk_level(t)
        neck[0] += 0.05 * talk_amt * math.sin(t * 9.0 + ph)
        neck[1] += 0.025 * talk_amt * math.sin(t * 5.3 + ph)
        if bs["moving"] > 0.5:
            neck[0] -= pelvis_rot[0] * 0.6

        pose["arm_r"], pose["arm_l"], pose["neck"] = tuple(arm_r), tuple(arm_l), tuple(neck)

        # ---- gestures override arms / add neck
        if active:
            ctx = GestureContext(self, t, mats, pose)
            for g, w, lt, dur, params in active:
                out = g.fn(ctx, lt, dur, params)
                for j in ("arm_r", "arm_l"):
                    if j in out:
                        pose[j] = tuple(mx.lerp(a, c, w) for a, c in zip(pose[j], out[j]))
                if "neck" in out:
                    pose["neck"] = tuple(a + c * w for a, c in zip(pose["neck"], out["neck"]))
                if "neck_set" in out:
                    pose["neck"] = tuple(mx.lerp(a, c, w) for a, c in zip(pose["neck"], out["neck_set"]))
                if "gaze" in out:
                    gaze = [mx.lerp(gaze[0], out["gaze"][0], w), mx.lerp(gaze[1], out["gaze"][1], w)]

        mats = joint_matrices(b, pose)
        pose["mats"] = mats
        pose["points"] = points(b, mats)
        pose["face"] = self._face(t, gaze, talk_amt)
        pose["badge"] = self._badge(t)
        pose["feet_planted"] = bs["planted"] if posture != "sit" and sitw == 0 else {"r": False, "l": False}
        pose["posture"] = posture
        pose["moving"] = bs["moving"]
        pose["where"] = bs.get("where")
        pose["sit"] = sitw
        return pose

    def _swing_phase(self, bs, t):
        return t * (9.0 if bs["run"] else 6.0)

    # ------------------------------------------------------------ look and face
    def _target_point(self, target, t):
        if target is None:
            return None
        if isinstance(target, (tuple, list)):
            return tuple(target)
        if isinstance(target, str) and self.stage:
            other = self.stage.performers.get(target)
            if other is not None:
                return other.anchor("eyes", t)
            pt = self.stage.points.get(target)
            if pt is not None:
                return pt
        return None

    def _look_angles(self, t, mats):
        if not self.looks:
            return None
        idx = bisect.bisect_right([l[0] for l in self.looks], t) - 1
        if idx < 0:
            return None

        def angles(target):
            p = self._target_point(target, t)
            if p is None:
                return (0.0, 0.0)
            neck = mats["neck"]
            piv = (neck[0][3], neck[1][3], neck[2][3] + 0.66 * self.body.s)
            d = to_local_dir(mats["pelvis"], mx.v_sub(p, piv))
            yaw = math.atan2(d[0], -d[1])
            pitch = math.atan2(-d[2], math.hypot(d[0], d[1]))
            return (pitch, yaw)

        t_new, tgt_new = self.looks[idx]
        new = angles(tgt_new)
        old = angles(self.looks[idx - 1][1]) if idx > 0 else (0.0, 0.0)
        head_u = mx.smooth((t - t_new) / 0.38)
        pitch = mx.lerp(old[0], new[0], head_u)
        yaw = mx.angle_lerp(old[1], new[1], head_u)
        cp, cy = mx.clamp(pitch, -0.55, 0.6), mx.clamp(yaw, -1.15, 1.15)
        # Eyes lead: they reach the new target immediately; the head catches up.
        gx = mx.clamp((new[1] - cy) / 0.55, -1, 1)
        gy = mx.clamp(-(new[0] - cp) / 0.45, -1, 1)
        return cp, cy, gx * 0.9, gy * 0.6

    def _talk_level(self, t):
        for t0, t1, env, efps in self.talks:
            if t0 <= t < t1:
                i = int((t - t0) * efps)
                return env[min(i, len(env) - 1)]
        return 0.0

    def _face(self, t, gaze, talk):
        expr = "neutral"
        t_expr = -1e9
        for tt, e in self.faces:
            if tt <= t + 1e-9:
                expr, t_expr = e, tt
        eyes, mouth, extra = face_code(expr)
        eye_name = EYES[eyes]
        # Blinks: periodic, at expression changes and at big look changes.
        blink = False
        if eye_name not in ("closed", "closed_happy", "closed_sad", "shut_tight"):
            if 0.0 <= t - t_expr < 0.12 and t_expr > 0:
                blink = True
            period = 2.6 + 1.9 * mx.hash01(self.seed, int(t / 3.1))
            phase = (t + self.seed * 0.13) % period
            if phase < 0.13:
                blink = True
            for tt, _ in self.looks:
                if 0.02 <= t - tt < 0.13:
                    blink = True
        if blink:
            eyes = EYES.index(CLOSED_OF.get(eye_name, "closed"))
        if talk > 0.04:
            m_name = MOUTHS[mouth]
            frame = int(t * FPS)
            if talk > 0.42:
                mouth = MOUTHS.index("talk_a" if (frame // 3) % 3 else "talk_o")
            elif talk > 0.16:
                mouth = MOUTHS.index("talk_e")
            else:
                mouth = MOUTHS.index("flat_small" if m_name not in ("smile", "soft_smile") else "soft_smile")
        return dict(eyes=eyes, mouth=mouth, extra=extra, gaze=(gaze[0], gaze[1]), expr=expr)

    def _badge(self, t):
        if not self.badges:
            return None
        text, t0 = None, None
        for tt, v in self.badges:
            if tt <= t + 1e-9:
                text, t0 = v, tt
        if text is None:
            return None
        pop = 1.0 + 0.35 * math.exp(-max(0.0, t - t0) * 9) * (1 if t0 > self.badges[0][0] else 0)
        return dict(text=text, pop=pop)


def to_local_dir(m, d):
    return (
        m[0][0] * d[0] + m[1][0] * d[1] + m[2][0] * d[2],
        m[0][1] * d[0] + m[1][1] * d[1] + m[2][1] * d[2],
        m[0][2] * d[0] + m[1][2] * d[1] + m[2][2] * d[2],
    )


def envelope(t, t0, t1, a, d):
    """Gesture weight: ramps up from its cue t0 over `a`, holds, then eases out after t1 over `d`."""
    if t < t0 or t > t1 + d:
        return 0.0
    if t > t1:
        return 1 - mx.smooth((t - t1) / d) if d > 0 else 0.0
    if a > 0 and t < t0 + a:
        return mx.smooth((t - t0) / a)
    return 1.0


# ===================================================================== gestures
class GestureContext:
    def __init__(self, performer, t, mats, pose):
        self.p, self.t, self.mats, self.pose = performer, t, mats, pose

    def target(self, spec):
        """Resolve a target: performer id with optional anchor 'kai.head', point name, or xyz."""
        if isinstance(spec, (tuple, list)):
            return tuple(spec)
        stage = self.p.stage
        if "." in spec:
            cid, anchor = spec.split(".", 1)
        else:
            cid, anchor = spec, "chest"
        if stage and cid in stage.performers:
            return stage.performers[cid].anchor(anchor, self.t)
        if stage and spec in stage.points:
            return stage.points[spec]
        raise KeyError(f"Unknown gesture target {spec}")

    def aim(self, arm, world_point, roll=0.0):
        piv = self.mats[arm]
        o = (piv[0][3], piv[1][3], piv[2][3])
        d = to_local_dir(self.mats["pelvis"], mx.v_sub(world_point, o))
        a, b, _ = aim_euler(d)
        return (a, b, roll)

    def self_point(self, local):
        """Point in the character's own pelvis space (studs)."""
        from .mathx import transform
        s = self.p.body.s
        return transform(self.mats["pelvis"], (local[0] * s, local[1] * s, local[2] * s))


class Gesture:
    def __init__(self, fn, blend_in=0.25, blend_out=0.3, lean=None, contacts=None, crouch=None):
        self.fn, self.blend_in, self.blend_out = fn, blend_in, blend_out
        self.lean, self.contacts, self.crouch = lean, contacts, crouch


def _osc(lt, hz, amp, phase=0.0):
    return amp * math.sin(mx.TAU * hz * lt + phase)


def g_wave(ctx, lt, dur, p):
    side = p.get("hand", "r")
    sign = 1 if side == "r" else -1
    arm = (-2.75, sign * -0.35 + _osc(lt, 1.8, 0.28) * sign, 0.0)
    return {"arm_" + side: arm}


def g_reach(ctx, lt, dur, p):
    side = p.get("hand", "r")
    tgt = ctx.target(p["to"])
    return {"arm_" + side: ctx.aim("arm_" + side, tgt)}


def g_point(ctx, lt, dur, p):
    side = p.get("hand", "r")
    tgt = ctx.target(p["at"])
    return {"arm_" + side: ctx.aim("arm_" + side, tgt), "neck": (0.05, 0, 0)}


def g_offer(ctx, lt, dur, p):
    side = p.get("hand", "r")
    a = (-1.35, 0.12 if side == "r" else -0.12, 0.0)
    out = {"arm_" + side: a}
    if p.get("both"):
        out["arm_l" if side == "r" else "arm_r"] = (-1.35, -0.12 if side == "r" else 0.12, 0.0)
    return out


def g_open_arms(ctx, lt, dur, p):
    spread = p.get("spread", 0.55)
    raise_ = p.get("raise", 1.25)
    return {"arm_r": (-raise_, 0.0, -spread), "arm_l": (-raise_, 0.0, spread)}


def g_hug(ctx, lt, dur, p):
    """Arms wrap the partner: the taller actor holds high (shoulders), the shorter one low (waist).

    Arms stay near-parallel so rigid R6 blocks do not cross the partner's arms or the
    actor's own torso; the hands aim just past the partner's flanks and behind their back.
    """
    at = p.get("at", "chest")
    tgt = ctx.target(p["who"] + "." + at)
    out = {}
    s = ctx.p.body.s
    yaw = ctx.pose["yaw"]
    nx, ny = math.cos(yaw), math.sin(yaw)
    fx, fy = math.sin(yaw), -math.cos(yaw)
    partner = ctx.p.stage.performers[p["who"]].body
    partner_half = partner.head_radius if at in ("head", "top", "eyes", "face") else 1.0 * partner.s * partner.build
    arm_half = 0.47 * s
    shoulder = 1.52 * s * ctx.p.body.build
    width = max(partner_half + arm_half + 0.035, shoulder * 0.95) + p.get("extra_width", 0.0)
    back = p.get("depth", 0.6) * s
    squeeze = 0.035 * math.sin(min(lt / 0.5, 1) * math.pi * 0.5)
    for side, sign in (("r", -1), ("l", 1)):
        pt = (tgt[0] + nx * sign * width + fx * back, tgt[1] + ny * sign * width + fy * back, tgt[2] + p.get("lift", 0.0))
        a, b, _ = ctx.aim("arm_" + side, pt)
        out["arm_" + side] = (a, b - sign * squeeze, 0.0)
    breathe = 0.02 * math.sin(lt * 2.1)
    out["neck_set"] = (p.get("head_pitch", 0.1) + breathe, 0.0, p.get("head_turn", 0.0))
    out["gaze"] = (0.0, -0.2)
    return out


def hug_contacts(p):
    return [("arm_*", p["who"] + ":arm_*", p.get("depth_ok", 0.13)), ("arm_*", p["who"] + ":*", 0.08),
            ("torso", p["who"] + ":*", 0.05), ("head", p["who"] + ":*", 0.07),
            ("arm_*", "self:torso", 0.07), ("arm_*", "self:head", 0.05)]


def hug_lean(lt, dur, p):
    return (p.get("lean", 0.08), 0.0)


def g_hands_face(ctx, lt, dur, p):
    face = ctx.p.anchor("face", ctx.t)
    out = {}
    yaw = ctx.pose["yaw"]
    s = ctx.p.body.s
    fx, fy = math.sin(yaw), -math.cos(yaw)
    nx, ny = math.cos(yaw), math.sin(yaw)
    for side, sign in (("r", -1), ("l", 1)):
        pt = (face[0] + fx * 0.75 * s + nx * sign * 0.25 * s, face[1] + fy * 0.75 * s + ny * sign * 0.25 * s, face[2] - 0.35 * s)
        out["arm_" + side] = ctx.aim("arm_" + side, pt)
    out["neck"] = (0.28 + _osc(lt, 3.2, 0.04) * p.get("shake", 1), 0.0, 0.0)
    return out


def g_sob(ctx, lt, dur, p):
    return {"neck": (0.22 + _osc(lt, 3.6, 0.07), 0.0, 0.0)}


def sob_lean(lt, dur, p):
    return (0.10 + abs(_osc(lt, 3.6, 0.04)), 0.0)


def g_head_down(ctx, lt, dur, p):
    return {"neck": (p.get("amount", 0.42), 0.0, 0.0), "arm_r": (0.04, -0.03, 0.0), "arm_l": (0.04, 0.03, 0.0), "gaze": (0.0, -0.4)}


def slump_lean(lt, dur, p):
    return (p.get("lean", 0.1), 0.0)


def g_nod(ctx, lt, dur, p):
    return {"neck": (abs(_osc(lt, 2.2, 0.22)), 0.0, 0.0)}


def g_shake_head(ctx, lt, dur, p):
    return {"neck": (0.06, 0.0, _osc(lt, 2.6, 0.32))}


def g_shrug(ctx, lt, dur, p):
    up = math.sin(min(1.0, lt / 0.3) * math.pi * 0.5)
    return {"arm_r": (-0.45 * up, 0.0, -0.45 * up), "arm_l": (-0.45 * up, 0.0, 0.45 * up), "neck": (0, 0.12 * up, 0)}


def g_ruffle(ctx, lt, dur, p):
    side = p.get("hand", "r")
    top = ctx.target(p["who"] + ".top")
    s = ctx.p.body.s
    yaw = ctx.pose["yaw"]
    nx, ny = math.cos(yaw), math.sin(yaw)
    fx, fy = math.sin(yaw), -math.cos(yaw)
    wob = _osc(lt, 2.5, 0.10 * s)
    pt = (top[0] - fx * 0.3 * s + nx * wob, top[1] - fy * 0.3 * s + ny * wob, top[2] + 0.35 * s + abs(_osc(lt, 2.5, 0.05 * s)))
    return {"arm_" + side: ctx.aim("arm_" + side, pt), "neck": (0.25, 0, 0)}


def ruffle_contacts(p):
    h = "arm_" + p.get("hand", "r")
    return [(h, p["who"] + ":head", 0.12), (h, p["who"] + ":arm_*", 0.1), (h, "self:head", 0.08)]


def g_hand_chest(ctx, lt, dur, p):
    side = p.get("hand", "r")
    pt = ctx.self_point((0.25 if side == "l" else -0.25, -1.6, 1.5))
    return {"arm_" + side: ctx.aim("arm_" + side, pt), "neck": (0.15, 0, 0)}


def self_contacts_arm(side):
    return lambda p: [("arm_" + p.get("hand", side), "self:torso", 0.08)]


def g_blocked(ctx, lt, dur, p):
    """Reach for a hug and get stopped by an invisible wall, then recoil."""
    stop = p.get("stop", 0.55)
    if lt < stop:
        r = mx.ease_out(lt / stop)
        arms = -1.35 * r
        spread = 0.5 * r
    else:
        q = mx.smooth((lt - stop) / 0.25)
        arms = mx.lerp(-1.35, -1.2, q) + 0.05 * math.sin((lt - stop) * 40) * math.exp(-(lt - stop) * 8)
        spread = 0.5 - 0.08 * q
    return {"arm_r": (arms, 0.0, -spread), "arm_l": (arms, 0.0, spread)}


def blocked_lean(lt, dur, p):
    stop = p.get("stop", 0.55)
    if lt < stop:
        return (0.14 * mx.ease_out(lt / stop), 0.0)
    return (0.14 - 0.12 * mx.smooth((lt - stop) / 0.3), 0.0)


def g_flinch(ctx, lt, dur, p):
    q = math.exp(-lt * 5)
    return {"arm_r": (-0.6 * q, 0, -0.2 * q), "arm_l": (-0.6 * q, 0, 0.2 * q), "neck": (-0.15 * q, 0, 0)}


def flinch_lean(lt, dur, p):
    return (-0.12 * math.exp(-lt * 5), 0.0)


def g_look_around(ctx, lt, dur, p):
    return {"neck": (0.0, 0.0, 0.55 * math.sin(mx.TAU * lt / max(dur, 0.5)))}


def g_hold_hand(ctx, lt, dur, p):
    side = p.get("hand", "r")
    tgt = ctx.target(p["who"] + ".hand_" + p.get("their", "l"))
    return {"arm_" + side: ctx.aim("arm_" + side, tgt)}


def g_hold_shoulders(ctx, lt, dur, p):
    """Hands resting on the partner's shoulders (arms reach forward, slightly down)."""
    out = {}
    for side, theirs in (("r", "l"), ("l", "r")):
        tgt = ctx.target(p["who"] + ".shoulder_" + theirs)
        tgt = (tgt[0], tgt[1], tgt[2] + p.get("lift", 0.12))
        out["arm_" + side] = ctx.aim("arm_" + side, tgt)
    out["neck_set"] = (p.get("head_pitch", 0.3), 0.0, 0.0)
    return out


def g_lean_forward(ctx, lt, dur, p):
    return {}


def lean_fwd(lt, dur, p):
    return (p.get("amount", 0.25), 0.0)


def g_hands_behind(ctx, lt, dur, p):
    return {"arm_r": (0.35, 0.0, 0.25), "arm_l": (0.35, 0.0, -0.25)}


def g_think(ctx, lt, dur, p):
    face = ctx.p.anchor("face", ctx.t)
    s = ctx.p.body.s
    yaw = ctx.pose["yaw"]
    fx, fy = math.sin(yaw), -math.cos(yaw)
    pt = (face[0] + fx * 0.7 * s, face[1] + fy * 0.7 * s, face[2] - 0.6 * s)
    return {"arm_r": ctx.aim("arm_r", pt), "neck": (0.05, 0, 0.12)}


def g_crouch_gesture(ctx, lt, dur, p):
    return {}


GESTURES = {
    "wave": Gesture(g_wave, 0.2, 0.3),
    "reach": Gesture(g_reach, 0.35, 0.35, contacts=lambda p: [("arm_" + p.get("hand", "r"), "*", p.get("depth_ok", 0.05))]),
    "point": Gesture(g_point, 0.25, 0.3),
    "offer": Gesture(g_offer, 0.3, 0.3),
    "open_arms": Gesture(g_open_arms, 0.35, 0.35, contacts=lambda p: [("arm_*", "*", 0.08)]),
    "hug": Gesture(g_hug, 0.45, 0.45, lean=hug_lean, contacts=hug_contacts),
    "hands_face": Gesture(g_hands_face, 0.35, 0.4, contacts=lambda p: [("arm_*", "self:head", 0.12), ("arm_*", "self:torso", 0.1), ("arm_r", "self:arm_l", 0.08)]),
    "sob": Gesture(g_sob, 0.3, 0.4, lean=sob_lean),
    "head_down": Gesture(g_head_down, 0.45, 0.45, lean=slump_lean),
    "nod": Gesture(g_nod, 0.1, 0.15),
    "shake_head": Gesture(g_shake_head, 0.1, 0.15),
    "shrug": Gesture(g_shrug, 0.15, 0.3),
    "ruffle": Gesture(g_ruffle, 0.3, 0.3, contacts=ruffle_contacts),
    "hand_chest": Gesture(g_hand_chest, 0.3, 0.35, contacts=self_contacts_arm("r")),
    "blocked_hug": Gesture(g_blocked, 0.2, 0.4, lean=blocked_lean),
    "flinch": Gesture(g_flinch, 0.05, 0.4, lean=flinch_lean),
    "look_around": Gesture(g_look_around, 0.2, 0.2),
    "hold_hand": Gesture(g_hold_hand, 0.3, 0.3, contacts=lambda p: [("arm_" + p.get("hand", "r"), p["who"] + ":arm_*", 0.08)]),
    "lean_forward": Gesture(g_lean_forward, 0.4, 0.4, lean=lean_fwd),
    "hold_shoulders": Gesture(g_hold_shoulders, 0.4, 0.4, lean=lambda lt, dur, p: (p.get("lean", 0.06), 0.0),
                              contacts=lambda p: [("arm_*", p["who"] + ":arm_*", 0.15), ("arm_*", p["who"] + ":*", 0.1)]),
    "hands_behind": Gesture(g_hands_behind, 0.3, 0.3),
    "think": Gesture(g_think, 0.3, 0.3, contacts=lambda p: [("arm_r", "self:head", 0.08)]),
    "crouch": Gesture(g_crouch_gesture, 0.35, 0.35, crouch=lambda lt, dur, p: 1.0),
}


class Stage:
    """All performers of an episode plus named world points (props, marks)."""

    def __init__(self):
        self.performers = {}
        self.points = {}

    def add(self, performer):
        performer.stage = self
        self.performers[performer.id] = performer
        return performer
