"""Layout and animation QC on a compiled plan, before anything renders.

Measures the exact poses the Blender build will bake:
  collision.set     a body part penetrates solid set geometry
  collision.actor   two actors interpenetrate outside a declared contact (hug, ruffle...)
  feet.slide        a planted foot moves on the floor
  feet.sink/hover   a standing actor is below/above the floor
  face.hidden       the shot subject's face is outside the frame, under the caption band or blocked
  camera.inside     the lens sits inside solid geometry
  cut.axis          two actors swap screen sides across a cut in the same scene (180-degree rule)
  cut.jump          consecutive shots of the same subject at the same size and angle
  film.variety      fewer than three shot sizes in the film
Errors block rendering; warnings need a note in the review.
"""

import fnmatch
import math

from . import mathx as mx
from .camera import project
from .geom import element_obb, overlap_depth, aabb_overlap, segment_hits_obb, point_in_obb
from .rig import part_boxes

BODY_TOL = 0.025     # metres of penetration ignored (bevels, rounding)
SLIDE_TOL = 0.022    # metres per frame for a planted foot corner
CAPTION_BAND = (0.24, 0.40)  # normalised screen y range used by captions


class Report:
    def __init__(self):
        self.issues = []

    def add(self, rule, severity, shot, t, msg, **extra):
        self.issues.append(dict(rule=rule, severity=severity, shot=shot, t=round(t, 3), msg=msg, **extra))

    def merged(self, fps=24):
        """Collapse per-frame repeats into frame ranges."""
        out = {}
        for i in self.issues:
            key = (i["rule"], i["shot"], i["msg"])
            if key in out:
                o = out[key]
                o["t1"] = max(o["t1"], i["t"])
                o["count"] += 1
                o["depth"] = max(o.get("depth", 0), i.get("depth", 0))
            else:
                out[key] = dict(i, t0=i["t"], t1=i["t"], count=1)
        res = sorted(out.values(), key=lambda x: (x["t0"], x["rule"]))
        for r in res:
            r.pop("t", None)
        return res

    @property
    def errors(self):
        return [i for i in self.merged() if i["severity"] == "error"]


def _cyl(box, other):
    """Re-orient a head box (a rounded cylinder) to face `other`, so turning the head
    does not swing phantom box corners into a partner."""
    from .rig import OBB
    z = box.axes[2]
    d = mx.v_sub(other.centre, box.centre)
    d = mx.v_sub(d, mx.v_scale(z, mx.v_dot(d, z)))
    if mx.v_len(d) < 1e-6:
        return box
    x = mx.v_norm(d)
    y = mx.v_cross(z, x)
    r = min(box.half[0], box.half[1])
    return OBB(box.name, box.centre, [x, y, z], (r, r, box.half[2]))


def _depth(a, an, b, bn):
    if an == "head":
        a = _cyl(a, b)
    if bn == "head":
        b = _cyl(b, a)
    return overlap_depth(a, b)


def _allowed(perf, t, my_part, other_cid, other_part, depth):
    for (t0, t1, mine, other, max_depth) in perf.allow:
        if not (t0 - 0.45 <= t <= t1 + 0.45):
            continue
        if not fnmatch.fnmatch(my_part, mine):
            continue
        if other == "*":
            if depth <= max_depth:
                return True
            continue
        ocid, _, opart = other.partition(":")
        if ocid == "self":
            ocid = perf.id
        if ocid != other_cid:
            continue
        if fnmatch.fnmatch(other_part, opart or "*") and depth <= max_depth:
            return True
    return False


def run(plan, step=1, window=None, shot_checks=True):
    """QC every `step`-th frame. `window=(t0, t1)` limits the frame checks to that span and
    `shot_checks=False` skips the camera/cutting checks (used by kit/repair.py to test
    candidates quickly; the layout gate always runs everything)."""
    rep = Report()
    perf = plan.stage.performers
    fps = plan.fps
    solids = {}
    for sid, s in plan.sets.items():
        lst = []
        for e in s.elements:
            if not e.get("solid"):
                continue
            b = element_obb(e, s.offset)
            if b is not None:
                lst.append((e, b, b.aabb()))
        solids[sid] = lst

    n_frames = int(plan.duration * fps)
    f0, f1 = (0, n_frames) if window is None else (max(0, int(window[0] * fps)), min(n_frames, int(window[1] * fps) + 1))
    prev_feet = {}
    for f in range(f0, f1, step):
        t = f / fps
        shot = plan.shot_at(t)
        sid = shot["set"] if shot else None
        poses = {}
        for cid, p in perf.items():
            pose = p.pose(t)
            if pose is None or pose.get("where") is None:
                continue
            boxes = part_boxes(p.body, pose["mats"], shrink=0.004)
            poses[cid] = (pose, boxes)
        # ---- actor vs set
        for cid, (pose, boxes) in poses.items():
            where = pose["where"]
            p = perf[cid]
            seat = None
            if pose["sit"] > 0:
                seat = p.base(t).get("seat")
            for pname, box in boxes.items():
                bb = box.aabb()
                for e, sb, sbb in solids[where]:
                    if not aabb_overlap(bb, sbb):
                        continue
                    d = _depth(box, pname, sb, "")
                    if d <= BODY_TOL:
                        continue
                    if seat and e.get("tag") in ("seat", "bed") and d < 0.06:
                        continue
                    if _allowed(p, t, pname, "set", e["name"], d):
                        continue
                    rep.add("collision.set", "error", shot["id"] if shot else None, t,
                            f"{cid}.{pname} inside '{e['name']}'", depth=round(d, 3))
        # ---- actor vs actor
        cids = list(poses)
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                a, b = cids[i], cids[j]
                pa, ba = poses[a]
                pb, bb_ = poses[b]
                if pa["where"] != pb["where"]:
                    continue
                if mx.v_len(mx.v_sub(pa["points"]["hips"], pb["points"]["hips"])) > 3.0:
                    continue
                for na, xa in ba.items():
                    for nb, xb in bb_.items():
                        if not aabb_overlap(xa.aabb(), xb.aabb()):
                            continue
                        d = _depth(xa, na, xb, nb)
                        if d <= BODY_TOL:
                            continue
                        if _allowed(perf[a], t, na, b, nb, d) or _allowed(perf[b], t, nb, a, na, d):
                            continue
                        rep.add("collision.actor", "error", shot["id"] if shot else None, t,
                                f"{a}.{na} inside {b}.{nb}", depth=round(d, 3))
        # ---- self collision (arms through own head/torso)
        for cid, (pose, boxes) in poses.items():
            p = perf[cid]
            for arm in ("arm_r", "arm_l"):
                for other in ("head", "torso"):
                    d = _depth(boxes[arm], arm, boxes[other], other)
                    if d > 0.2 and not _allowed(p, t, arm, cid, other, d):
                        rep.add("collision.self", "warn", shot["id"] if shot else None, t, f"{cid}.{arm} inside own {other}", depth=round(d, 3))
        # ---- feet
        for cid, (pose, boxes) in poses.items():
            if pose["sit"] > 0 or pose["posture"] == "crouch":
                prev_feet.pop(cid, None)
                continue
            floor = perf[cid].base(t)["floor"]
            low = min(boxes["leg_r"].lowest(), boxes["leg_l"].lowest())
            if low < floor - 0.03:
                rep.add("feet.sink", "error", shot["id"] if shot else None, t, f"{cid} feet {floor - low:.2f} m under the floor")
            if low > floor + 0.04 and all(pose["feet_planted"].values()):
                rep.add("feet.hover", "warn", shot["id"] if shot else None, t, f"{cid} hovering {low - floor:.2f} m")
            corners = {side: pose["points"]["foot_" + side] for side in ("r", "l")}
            if cid in prev_feet and step == 1:
                pf, pplant = prev_feet[cid]
                for side in ("r", "l"):
                    if pose["feet_planted"].get(side) and pplant.get(side):
                        dxy = math.hypot(corners[side][0] - pf[side][0], corners[side][1] - pf[side][1])
                        if SLIDE_TOL < dxy < 1.0 and pose["points"]["foot_" + side][2] < floor + 0.05:
                            rep.add("feet.slide", "error", shot["id"] if shot else None, t, f"{cid} {side} foot slides", depth=round(dxy, 3))
            prev_feet[cid] = (corners, dict(pose["feet_planted"]))

    if shot_checks:
        _shot_checks(plan, rep)
    return rep


def _shot_checks(plan, rep):
    perf = plan.stage.performers
    sizes = set()
    prev = None
    for shot in plan.shots:
        cam = shot.get("camera")
        if not cam:
            continue
        sizes.add(cam["size"])
        for msg in cam.get("report", []):
            close = cam["size"] in ("ecu", "cu", "mcu", "ms", "ots")
            sev = "error" if (("blocks" in msg or "out of frame" in msg) and close) or "inside" in msg else "warn"
            rep.add("camera.solve", sev, shot["id"], shot["t0"], msg)
        # Caption band check for close shots: the mouth should sit above the captions.
        if cam["size"] in ("cu", "ecu", "mcu"):
            t = (shot["t0"] + shot["t1"]) / 2
            fr = cam["frames"][len(cam["frames"]) // 2]
            for sid in cam["subjects"][:1]:
                pts = perf[sid].pose(t)["points"]
                pr = project(fr[1], fr[2], fr[3], pts["eyes"])
                if pr and pr[1] < CAPTION_BAND[1] + 0.08:
                    rep.add("face.caption", "warn", shot["id"], t, f"{sid}'s eyes at y={pr[1]:.2f} sit near the caption band")
        # 180-degree rule across cuts in the same scene
        if prev and prev["scene"] == shot["scene"]:
            pc, cc = prev["camera"], cam
            common = [c for c in perf if perf[c].where(shot["t0"]) == shot["set"]]
            order_prev = _screen_order(plan, prev, pc, common, prev["t1"] - 0.02)
            order_cur = _screen_order(plan, shot, cc, common, shot["t0"] + 0.02)
            both = [c for c in order_prev if c in order_cur]
            if len(both) >= 2:
                a, b = both[0], both[1]
                if (order_prev[a] < order_prev[b]) != (order_cur[a] < order_cur[b]):
                    if abs(order_prev[a] - order_prev[b]) > 0.08 and abs(order_cur[a] - order_cur[b]) > 0.08:
                        rep.add("cut.axis", "warn", shot["id"], shot["t0"], f"{a} and {b} swap screen sides across the cut from {prev['id']}")
            if pc["size"] == cc["size"] and pc["subjects"][:1] == cc["subjects"][:1] and abs(pc["side"] - cc["side"]) < 20:
                rep.add("cut.jump", "warn", shot["id"], shot["t0"], f"same size and angle as {prev['id']} on {cc['subjects'][:1]}")
        prev = shot
    if len(sizes) < 3:
        rep.add("film.variety", "error", None, 0, f"only {len(sizes)} shot sizes")


def _screen_order(plan, shot, cam, cids, t):
    fr = min(cam["frames"], key=lambda f: abs(f[0] - t))
    out = {}
    for c in cids:
        pose = plan.stage.performers[c].pose(t)
        if pose is None:
            continue
        pr = project(fr[1], fr[2], fr[3], pose["points"]["eyes"])
        if pr and 0 <= pr[0] <= 1 and 0 <= pr[1] <= 1:
            out[c] = pr[0]
    return out
