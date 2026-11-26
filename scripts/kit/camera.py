"""Shot camera solver for vertical 9:16 frames.

A shot asks for a size (ecu, cu, mcu, ms, fs, ws, ews, insert, ots, two), a
subject, a side angle relative to the subject's facing, a height and a move.
The solver places the lens, keeps the eyes on the upper third, tries nearby
angles when geometry blocks the face or the lens lands inside furniture, and
lists the wild walls (and ceilings) that must be hidden for the shot.
"""

import math

from . import mathx as mx
from .geom import element_obb, point_in_obb, segment_hits_obb
from .rig import part_boxes

SENSOR = 36.0  # mm, fitted to the vertical (long) side of the 9:16 frame
ASPECT = 9 / 16
# Visible frame height in studs of the subject. A 9:16 frame is width-limited and R6
# heads are wide (1.3 studs), so a close-up shows head and shoulders, not just the face.
SIZE_STUDS = {"ecu": 2.5, "cu": 3.4, "mcu": 4.7, "ms": 6.4, "fs": 8.6, "ws": 14.0, "ews": 24.0}
LENS = {"ecu": 75, "cu": 58, "mcu": 48, "ms": 38, "fs": 30, "ws": 24, "ews": 20, "insert": 55, "ots": 40, "two": 35}
EYE_AT = {"ecu": 0.55, "cu": 0.6, "mcu": 0.66, "ms": 0.72}
FSTOP = {"ecu": 2.0, "cu": 2.2, "mcu": 2.8, "ms": 3.5, "fs": 5.6, "ws": 8, "ews": 11, "insert": 2.4, "ots": 2.8, "two": 4.0}
HEIGHTS = {"eye": 0.0, "low": -14.0, "high": 24.0, "top": 62.0, "worm": -30.0, "slight_high": 10.0, "slight_low": -7.0}


def vfov(lens):
    return 2 * math.atan(SENSOR / 2 / lens)


def hfov(lens):
    return 2 * math.atan(SENSOR / 2 * ASPECT / lens)


def look_rotation(loc, aim):
    d = mx.v_sub(aim, loc)
    horiz = math.hypot(d[0], d[1])
    return (math.atan2(horiz, -d[2]), 0.0, math.atan2(-d[0], d[1]))


def project(cam_loc, cam_aim, lens, p):
    """Normalised screen coords (x right 0..1, y up 0..1) of world point p, and depth."""
    fwd = mx.v_norm(mx.v_sub(cam_aim, cam_loc))
    right = mx.v_norm(mx.v_cross(fwd, (0, 0, 1)))
    up = mx.v_cross(right, fwd)
    d = mx.v_sub(p, cam_loc)
    z = mx.v_dot(d, fwd)
    if z <= 1e-4:
        return None
    x = mx.v_dot(d, right) / z
    y = mx.v_dot(d, up) / z
    tx = math.tan(hfov(lens) / 2)
    ty = math.tan(vfov(lens) / 2)
    return (0.5 + x / tx / 2, 0.5 + y / ty / 2, z)


class Solver:
    def __init__(self, plan):
        self.plan = plan

    # ------------------------------------------------------------------ helpers
    def solids(self, set_, include_cut=True):
        out = []
        for e in set_.elements:
            if not e.get("solid") and not e.get("cut"):
                continue
            b = element_obb(e, set_.offset)
            if b is None:
                continue
            out.append((e, b))
        return out

    def actor_boxes(self, t, exclude=()):
        out = []
        for cid, p in self.plan.stage.performers.items():
            if cid in exclude:
                continue
            pose = p.pose(t, gestures=True)
            if pose is None or pose.get("where") is None:
                continue
            for name, b in part_boxes(p.body, pose["mats"]).items():
                if name == "head":
                    from .rig import OBB
                    pad = 0.18 * p.body.s
                    b = OBB(b.name, mx.v_add(b.centre, mx.v_scale(b.axes[2], pad * 0.6)), b.axes, (b.half[0] + pad * 0.5, b.half[1] + pad * 0.5, b.half[2] + pad * 0.6))
                out.append((cid + ":" + name, b))
        return out

    def subject_points(self, subjects, t):
        pts = []
        for s in subjects:
            if s in self.plan.stage.performers:
                p = self.plan.stage.performers[s].pose(t)
                pts.append(p["points"])
            else:
                pts.append(None)
        return pts

    # ------------------------------------------------------------------ solve
    def solve(self, shot):
        cam = dict(shot["cam"])
        set_ = self.plan.sets[shot["set"]]
        t0, t1 = shot["t0"], shot["t1"]
        size = cam.get("size", "mcu")
        subjects = cam.get("on", [])
        if isinstance(subjects, str):
            subjects = [subjects]
        move = cam.get("move", "drift")
        base_side = cam.get("side", 0.0)
        tries = [0, 12, -12, 24, -24, 36, -36, 50, -50, 65, -65]
        if cam.get("locked"):
            tries = [0]
        cands = []
        for el in (0, 10, 20):
            for dist_scale in (1.0, 0.85, 0.72):
                for dside in tries:
                    cost = abs(dside) + 1.6 * el + 60 * (1 - dist_scale)
                    cands.append((cost, el, dist_scale, dside))
        cands.sort()
        report = []
        best = None
        for cost, el, dist_scale, dside in cands:
            c2 = dict(cam, raise_deg=el)
            sol = self._place(c2, set_, shot, size, subjects, base_side + dside, dist_scale, move)
            problems = self._validate(sol, set_, shot, subjects, cam)
            if not problems:
                best = sol
                if cost:
                    report.append(f"camera adjusted by {dside:+d} deg, +{el} deg up, distance x{dist_scale} to clear the view")
                break
            if best is None or len(problems) < len(best.get("problems", ["x"] * 99)):
                best = dict(sol, problems=problems)
        if best.get("problems"):
            report += best["problems"]
        best["report"] = report
        best["hide"] = self._walls_to_hide(best, set_, shot, subjects)
        return best

    def _subject_frame(self, subjects, t):
        pts = [p for p in self.subject_points(subjects, t) if p]
        if not pts:
            return None
        eyes = tuple(sum(p["eyes"][i] for p in pts) / len(pts) for i in range(3))
        feet = min(min(p["foot_r"][2], p["foot_l"][2]) for p in pts)
        top = max(p["top"][2] for p in pts)
        face_dir = pts[0]["face_dir"]
        return dict(eyes=eyes, feet=feet, top=top, face_dir=face_dir, pts=pts)

    def _place(self, cam, set_, shot, size, subjects, side_deg, dist_scale, move):
        t0, t1 = shot["t0"], shot["t1"]
        tm = (t0 + t1) / 2
        lens = cam.get("lens", LENS.get(size, 40))
        keys = []
        perf = self.plan.stage.performers

        def frame_at(t, side, dscale, lens_):
            if size == "insert":
                target = cam["at"]
                p = self.plan.point(target, set_, t)
                fwd_side = math.radians(side)
                yaw0 = math.radians(cam.get("yaw", 0.0))
                d = cam.get("dist", 0.6) * dscale
                el = math.radians(HEIGHTS.get(cam.get("height", "high"), 20))
                dirv = (math.sin(yaw0 + fwd_side) * math.cos(el), -math.cos(yaw0 + fwd_side) * math.cos(el), math.sin(el))
                loc = mx.v_add(p, mx.v_scale(dirv, d))
                return loc, p, lens_
            fr = self._subject_frame(subjects, t)
            H = perf[subjects[0]].body.height
            if size == "ots":
                fg, bg = subjects[0], subjects[1]
                a = perf[fg].pose(t)["points"]
                b = perf[bg].pose(t)["points"]
                to_b = mx.v_norm((b["eyes"][0] - a["eyes"][0], b["eyes"][1] - a["eyes"][1], 0))
                left = (-to_b[1], to_b[0], 0)
                sgn = 1 if side >= 0 else -1
                s_ = perf[fg].body.s
                loc = mx.v_add(a["eyes"], mx.v_add(mx.v_scale(to_b, -1.9 * s_ * dscale - 0.55), mx.v_scale(left, sgn * 1.35 * s_)))
                loc = (loc[0], loc[1], a["eyes"][2] + 0.06)
                aim = (b["eyes"][0], b["eyes"][1], b["eyes"][2] - 0.08)
                return loc, aim, lens_
            if size == "two":
                pa, pb = fr["pts"][0], fr["pts"][-1]
                mid = mx.v_lerp(pa["eyes"], pb["eyes"], 0.5)
                sep = math.hypot(pa["eyes"][0] - pb["eyes"][0], pa["eyes"][1] - pb["eyes"][1])
                line = mx.v_norm((pb["eyes"][0] - pa["eyes"][0], pb["eyes"][1] - pa["eyes"][1], 0))
                perp = (line[1], -line[0], 0)
                # Choose the perpendicular on the side the first subject faces toward.
                if mx.v_dot(perp, pa["face_dir"]) + mx.v_dot(perp, pb["face_dir"]) < 0:
                    perp = mx.v_scale(perp, -1)
                ang = math.radians(side)
                dirv = (perp[0] * math.cos(ang) - perp[1] * math.sin(ang), perp[0] * math.sin(ang) + perp[1] * math.cos(ang), 0)
                width = sep + 1.1 * perf[subjects[0]].body.s * 2.5
                d = max(width / 2 / math.tan(hfov(lens_) / 2) / 0.8, (fr["top"] - fr["feet"]) * 0.55 / math.tan(vfov(lens_) / 2))
                d *= dscale * cam.get("dist_mul", 1.0)
                vh = 2 * d * math.tan(vfov(lens_) / 2)
                aim_z = mid[2] - (0.64 - 0.5) * vh
                el = math.radians(HEIGHTS.get(cam.get("height", "eye"), 0) + cam.get("raise_deg", 0))
                aim = (mid[0], mid[1], aim_z)
                loc = (aim[0] + dirv[0] * d * math.cos(el), aim[1] + dirv[1] * d * math.cos(el), mid[2] + d * math.sin(el) + cam.get("raise", 0.0))
                return loc, aim, lens_
            vh = SIZE_STUDS[size] * perf[subjects[0]].body.s
            d = vh / 2 / math.tan(vfov(lens_) / 2) * dscale * cam.get("dist_mul", 1.0)
            vh = 2 * d * math.tan(vfov(lens_) / 2)
            if size in EYE_AT:
                aim_z = fr["eyes"][2] - (EYE_AT[size] - 0.5) * vh
            else:
                floor = perf[subjects[0]].base(t)["floor"]
                aim_z = floor + vh * (0.5 - 0.16)
                aim_z = min(max(aim_z, floor + (fr["top"] - floor) * 0.5), floor + 1.6)
            rel = cam.get("relative", "body")
            if rel == "face":
                fd = fr["face_dir"]
                yaw_face = math.atan2(fd[0], -fd[1])
            elif rel == "body":
                yaw_face = perf[subjects[0]].base(t)["yaw"]
            else:
                yaw_face = 0.0
            if "world_yaw" in cam:
                yaw_face = math.radians(cam["world_yaw"])
            ang = yaw_face + math.radians(side)
            dirv = (math.sin(ang), -math.cos(ang), 0)
            el = math.radians(HEIGHTS.get(cam.get("height", "eye"), 0) + cam.get("raise_deg", 0))
            centre = (fr["eyes"][0], fr["eyes"][1], aim_z)
            # Lead room: shift the aim sideways opposite the look direction.
            lead = cam.get("lead", 0.0)
            right = (-dirv[1], dirv[0], 0)
            centre = mx.v_add(centre, mx.v_scale(right, lead * vh * ASPECT))
            loc = (centre[0] + dirv[0] * d * math.cos(el), centre[1] + dirv[1] * d * math.cos(el), centre[2] + d * math.sin(el) + cam.get("raise", 0.0))
            return loc, centre, lens_

        mv = move
        frames = []
        if mv == "track":
            n = max(2, int((t1 - t0) * 24) + 1)
            for i in range(n):
                t = t0 + (t1 - t0) * i / (n - 1)
                loc, aim, ln = frame_at(min(t, t1 - 0.03), side_deg, dist_scale, lens)
                frames.append((t, loc, aim, ln))
            sm = []
            for i in range(n):
                lo, hi = max(0, i - 6), min(n, i + 7)
                avg = lambda k: tuple(sum(frames[j][k][c] for j in range(lo, hi)) / (hi - lo) for c in range(3))
                sm.append((frames[i][0], avg(1), avg(2), frames[i][3]))
            frames = sm
        else:
            # One framing at the shot's key moment; moves are built around that same aim.
            tk = t0 + (t1 - t0) * cam.get("key", 0.6)
            loc, aim, ln = frame_at(min(tk, t1 - 0.03), side_deg, dist_scale, lens)
            a0, a1 = {"push": (1.14, 0.94), "pull": (0.93, 1.13), "push_fast": (1.3, 0.92), "drift": (1.02, 0.99),
                      "static": (1.0, 1.0), "arc": (1.0, 1.0), "rise": (1.0, 1.0)}.get(mv, (1.0, 1.0))
            arc = math.radians(cam.get("arc", 10.0 if mv == "arc" else 0.0))
            off = mx.v_sub(loc, aim)

            def orbit(v, ang):
                c, s_ = math.cos(ang), math.sin(ang)
                return (v[0] * c - v[1] * s_, v[0] * s_ + v[1] * c, v[2])
            st = mx.v_add(aim, mx.v_scale(orbit(off, -arc / 2), a0))
            en = mx.v_add(aim, mx.v_scale(orbit(off, arc / 2), a1))
            if mv == "drift":
                fwd = mx.v_norm(mx.v_sub(aim, loc))
                right = mx.v_norm(mx.v_cross(fwd, (0, 0, 1)))
                amt = 0.035 * mx.v_len(off) * (1 if (sum(map(ord, shot["id"])) % 2) else -1)
                st = mx.v_add(st, mx.v_scale(right, -amt))
                en = mx.v_add(en, mx.v_scale(right, amt))
            if mv == "rise":
                st = mx.v_add(st, (0, 0, -0.22))
                en = mx.v_add(en, (0, 0, 0.12))
            frames = [(t0, st, aim, ln), (t1, en, aim, ln)]
        focus = subjects[0] if subjects else None
        return dict(frames=frames, size=size, subjects=subjects, focus=focus, fstop=cam.get("fstop", FSTOP.get(size, 4)),
                    side=side_deg, handheld=cam.get("handheld", 0.35 if mv != "static" else 0.0), move=mv)

    def _validate(self, sol, set_, shot, subjects, cam):
        problems = []
        solids = self.solids(set_)
        times = [shot["t0"] + 0.05, (shot["t0"] + shot["t1"]) / 2, shot["t1"] - 0.05]
        fr = sol["frames"]
        samples = []
        for i in range(7):
            u = i / 6
            if len(fr) == 2:
                uu = u * u * (3 - 2 * u)
                samples.append((shot["t0"] + (shot["t1"] - shot["t0"]) * u, mx.v_lerp(fr[0][1], fr[1][1], uu), mx.v_lerp(fr[0][2], fr[1][2], uu), fr[0][3]))
            else:
                samples.append(fr[min(len(fr) - 1, int(u * (len(fr) - 1)))])
        for (t, loc, aim, lens) in samples:
            t = min(max(t, shot["t0"] + 0.02), shot["t1"] - 1.0 / 24)
            for e, b in solids:
                if e.get("cut"):
                    continue
                if point_in_obb(loc, b, 0.12):
                    problems.append(f"camera inside '{e['name']}'")
            if set_.camera_bounds:
                x0, y0, x1, y1 = set_.camera_bounds
                if not (x0 <= loc[0] - set_.offset[0] <= x1 and y0 <= loc[1] - set_.offset[1] <= y1):
                    problems.append("camera outside the set's camera bounds")
            if sol["size"] == "insert":
                continue
            # Line of sight to every subject's face.
            excl = set(subjects)
            if sol["size"] == "ots":
                excl.add(subjects[0])
            others = self.actor_boxes(t, exclude=excl)
            for sid in subjects[1:] if sol["size"] == "ots" else subjects:
                pts = self.plan.stage.performers[sid].pose(t)["points"]
                for key in ("eyes",):
                    tgt = pts[key]
                    pr = project(loc, aim, lens, tgt)
                    if pr is None or not (0.04 < pr[0] < 0.96 and 0.05 < pr[1] < 0.97):
                        problems.append(f"{sid} face out of frame at t={t:.2f}")
                        break
                    tgt = mx.v_lerp(tgt, loc, 0.06)
                    for e, b in solids:
                        if e.get("cut") or e.get("see_through"):
                            continue
                        if segment_hits_obb(loc, tgt, b, shrink=0.01):
                            problems.append(f"'{e['name']}' blocks {sid}'s face")
                            break
                    for name, b in others:
                        if segment_hits_obb(loc, tgt, b, shrink=0.01):
                            problems.append(f"{name} blocks {sid}'s face")
                            break
        # dedupe
        seen, out = set(), []
        for p in problems:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _walls_to_hide(self, sol, set_, shot, subjects):
        hide = set()
        locs = [f[1] for f in sol["frames"]]
        aims = [f[2] for f in sol["frames"]]
        for e in set_.elements:
            cut = e.get("cut")
            if not cut:
                continue
            b = element_obb(e, set_.offset)
            n = tuple(cut) + ((0,) if len(cut) == 2 else ())
            for loc, aim in zip(locs, aims):
                rel = mx.v_sub(loc, b.centre)
                if mx.v_dot(rel, n) < 0 and abs(mx.v_dot(rel, n)) > b.half[0] * 0 - 1e-3:
                    # Camera on the outside of this wall plane.
                    if n[2] != 0 or self._faces_wall(loc, aim, b, n):
                        hide.add(e["name"])
                if segment_hits_obb(loc, aim, b):
                    hide.add(e["name"])
        return sorted(hide)

    @staticmethod
    def _faces_wall(loc, aim, b, n):
        return True
