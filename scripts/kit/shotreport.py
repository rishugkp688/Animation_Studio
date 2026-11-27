"""A text description of every shot, for reviewers who cannot see images.

    python3 scripts/studio.py layout SLUG     # also writes renders/SLUG/reports/shots.txt and shots.json

For each shot, at its key moment (the storyboard frame): where each actor is on screen, how
much of the frame they fill, which way their face points relative to the camera, what blocks
it, their expression, and the lines spoken. Flags what a storyboard reviewer would catch:
an empty frame, a subject not visible, a face seen from behind in a close shot, a face
behind something.

This is a measurement of the plan, not of the rendered picture: it cannot judge lighting,
composition, emotion or anything the renderer adds. It does not replace looking at the
storyboard; a model that cannot see images must say so in its review.
"""

import math

from . import mathx as mx
from .camera import project
from .geom import segment_hits_obb
from .rig import part_boxes

CLOSE = ("ecu", "cu", "mcu", "ots")
WIDE = ("ws", "ews")
MIN_FILL = 0.02       # an actor filling less than this is "barely visible"
EMPTY_FILL = 0.03     # a non-wide shot whose actors fill less than this in total is "mostly set"


def _cam_at(cam, t, t0, t1):
    fr = cam["frames"]
    if len(fr) == 2:
        u = 0.0 if t1 <= t0 else min(max((t - t0) / (t1 - t0), 0.0), 1.0)
        uu = u * u * (3 - 2 * u)
        return mx.v_lerp(fr[0][1], fr[1][1], uu), mx.v_lerp(fr[0][2], fr[1][2], uu), fr[0][3]
    f = min(fr, key=lambda f: abs(f[0] - t))
    return f[1], f[2], f[3]


def _screen_box(loc, aim, lens, boxes):
    xs, ys = [], []
    for b in boxes.values():
        for c in b.corners():
            pr = project(loc, aim, lens, c)
            if pr is not None:
                xs.append(pr[0])
                ys.append(pr[1])
    if not xs:
        return None
    x0, x1 = max(0.0, min(xs)), min(1.0, max(xs))
    y0, y1 = max(0.0, min(ys)), min(1.0, max(ys))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _facing(face_dir, head, loc):
    to_cam = mx.v_norm(mx.v_sub(loc, head))
    fd = mx.v_norm((face_dir[0], face_dir[1], 0.0))
    tc = mx.v_norm((to_cam[0], to_cam[1], 0.0))
    ang = math.degrees(math.acos(max(-1.0, min(1.0, mx.v_dot(fd, tc)))))
    if ang < 35:
        return "front", ang
    if ang < 70:
        return "three-quarter", ang
    if ang < 110:
        return "profile", ang
    return "back of head", ang


def _where(x):
    return "left" if x < 0.36 else ("right" if x > 0.64 else "centre")


def describe(plan, sp):
    """List of per-shot dicts. `plan` must have solved cameras."""
    lines = {L["id"]: L for L in sp.get("lines", [])}
    perf = plan.stage.performers
    out = []
    for shot in plan.shots:
        cam = shot.get("camera")
        if not cam:
            continue
        t0, t1 = shot["t0"], shot["t1"]
        key = shot.get("cam", {}).get("key", 0.6)
        t = t0 + (t1 - t0) * key
        loc, aim, lens = _cam_at(cam, t, t0, t1)
        set_ = plan.sets[shot["set"]]
        solids = [(e, b) for e, b in _solids(set_)]
        actors, flags = [], []
        poses = {}
        for cid, p in perf.items():
            pose = p.pose(t)
            if pose is None or pose.get("where") != shot["set"]:
                continue
            poses[cid] = (p, pose, part_boxes(p.body, pose["mats"]))
        total = 0.0
        for cid, (p, pose, boxes) in poses.items():
            pts = pose["points"]
            eyes = project(loc, aim, lens, pts["eyes"])
            box = _screen_box(loc, aim, lens, boxes)
            fill = 0.0 if box is None else (box[2] - box[0]) * (box[3] - box[1])
            total += fill
            facing, ang = _facing(pts["face_dir"], pts["head"], loc)
            blocked = None
            if eyes is not None:
                tgt = mx.v_lerp(pts["eyes"], loc, 0.06)
                for e, b in solids:
                    if not e.get("cut") and not e.get("see_through") and segment_hits_obb(loc, tgt, b, shrink=0.01):
                        blocked = e["name"]
                        break
                if blocked is None:
                    for oid, (_, _, ob) in poses.items():
                        if oid == cid:
                            continue
                        hit = next((n for n, b in ob.items() if segment_hits_obb(loc, tgt, b, shrink=0.01)), None)
                        if hit:
                            blocked = f"{oid}'s {hit}"
                            break
            in_frame = eyes is not None and 0.0 <= eyes[0] <= 1.0 and 0.0 <= eyes[1] <= 1.0
            expr = next((e for tt, e in reversed(p.faces) if tt <= t), None)
            a = dict(id=cid, fill=round(fill, 3), height=round(0 if box is None else box[3] - box[1], 2),
                     eyes=None if eyes is None else (round(eyes[0], 2), round(eyes[1], 2)),
                     face_in_frame=in_frame, facing=facing, facing_deg=round(ang), face_blocked_by=blocked, expr=expr,
                     subject=cid in cam["subjects"])
            actors.append(a)
            if a["subject"]:
                if not in_frame or fill < MIN_FILL:
                    flags.append(f"subject {cid} is not visible" if not in_frame else f"subject {cid} fills only {fill:.0%} of the frame")
                elif cam["size"] in CLOSE and facing in ("back of head", "profile") and not (cam["size"] == "ots" and cid == cam["subjects"][0]):
                    flags.append(f"subject {cid} seen {facing} in a {cam['size']}")
                if blocked and in_frame:
                    flags.append(f"subject {cid}'s face is behind {blocked}")
        if cam["size"] not in WIDE and total < EMPTY_FILL:
            flags.append(f"mostly set: actors fill {total:.0%} of the frame")
        said = []
        for lid in shot.get("lines", []):
            lid = lid["id"] if isinstance(lid, dict) else lid
            L = lines.get(lid)
            if L:
                said.append(dict(id=lid, who=L.get("who"), text=L.get("text")))
        out.append(dict(id=shot["id"], beat=shot.get("beat"), t0=round(t0, 2), t1=round(t1, 2), key_t=round(t, 2),
                        size=cam["size"], subjects=cam["subjects"], side=round(cam.get("side", 0)), move=cam.get("move"),
                        lines=said, actors=sorted(actors, key=lambda a: -a["fill"]), flags=flags))
    return out


def _solids(set_):
    from .geom import element_obb
    for e in set_.elements:
        if not e.get("solid") and not e.get("cut"):
            continue
        b = element_obb(e, set_.offset)
        if b is not None:
            yield e, b


def to_text(shots, title=""):
    rows = [f"SHOT REPORT {title}".rstrip(), "Measured from the plan at each shot's key moment (the storyboard frame).",
            "It cannot judge lighting, composition or emotion: look at the storyboard if you can.", ""]
    n_flag = sum(1 for s in shots if s["flags"])
    rows.append(f"{len(shots)} shots, {n_flag} flagged.")
    rows.append("")
    for s in shots:
        subj = "+".join(s["subjects"])
        rows.append(f"{s['id']}  {s['size']:<4} on {subj}  [{s['beat']}]  {s['t0']:.1f}-{s['t1']:.1f}s  side {s['side']}  {s['move']}")
        for L in s["lines"]:
            rows.append(f"    says  {L['who']}: \"{L['text']}\"")
        off = [a["id"] for a in s["actors"] if a["fill"] == 0 and not a["face_in_frame"]]
        for a in s["actors"]:
            if a["id"] in off:
                continue
            pos = f"{_where(a['eyes'][0])} {a['eyes'][0]:.2f},{a['eyes'][1]:.2f}" if a["face_in_frame"] else "face off screen"
            extra = f", face blocked by {a['face_blocked_by']}" if a["face_blocked_by"] else ""
            ex = f", {a['expr']}" if a["expr"] else ""
            rows.append(f"    {a['id']:<10} {pos:<20} fills {a['fill']:.0%} (height {a['height']:.0%}), face {a['facing']}{extra}{ex}")
        if off:
            rows.append(f"    off screen: {', '.join(off)}")
        for f in s["flags"]:
            rows.append(f"    !  {f}")
        rows.append("")
    return "\n".join(rows)
