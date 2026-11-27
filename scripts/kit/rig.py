"""R6 blocky character rig: proportions, joint hierarchy and forward kinematics.

Classic Roblox R6 proportions in studs: legs 1x1x2, torso 2x1x2, arms 1x1x2 and
a rounded cylinder head. One stud is STUD metres at scale 1. Every character in
every episode uses this rig, so motion, contact and collision code is shared.

Hierarchy (Blender objects use the same names and transforms):
    root   at the ground under the hips; rotation = body lean (x, y) and yaw (z)
    pelvis child of root, at hip height; the torso box sits on top of it
    neck   child of pelvis at the top of the torso; the head hangs off it
    arm_r / arm_l   shoulder pivots, child of pelvis
    leg_r / leg_l   hip pivots, child of pelvis

The character faces local -Y. Its right side is -X, left side +X.
Positive X rotation on a limb swings its free end backward (+Y); forward is negative.
Positive X on the neck tilts the face down. Positive Z yaw turns toward the character's left.
"""

import math

from .mathx import compose, mat_mul, transform, rotate, axes, v_sub, v_len

STUD = 0.33

# Box parts: (parent joint, centre offset from the joint in studs, size in studs)
PARTS = {
    "torso": ("pelvis", (0.0, 0.0, 1.0), (2.0, 1.0, 2.0)),
    "head": ("neck", (0.0, 0.0, 0.66), (1.30, 1.22, 1.28)),
    "arm_r": ("arm_r", (0.0, 0.0, -0.55), (0.94, 0.96, 2.0)),
    "arm_l": ("arm_l", (0.0, 0.0, -0.55), (0.94, 0.96, 2.0)),
    "leg_r": ("leg_r", (0.0, 0.0, -1.0), (0.97, 0.98, 2.0)),
    "leg_l": ("leg_l", (0.0, 0.0, -1.0), (0.97, 0.98, 2.0)),
}

# Joint pivots relative to their parent, in studs.
JOINTS = {
    "pelvis": ("root", (0.0, 0.0, 2.0)),
    "neck": ("pelvis", (0.0, 0.0, 2.0)),
    "arm_r": ("pelvis", (-1.52, 0.0, 1.55)),
    "arm_l": ("pelvis", (1.52, 0.0, 1.55)),
    "leg_r": ("pelvis", (-0.5, 0.0, 0.0)),
    "leg_l": ("pelvis", (0.5, 0.0, 0.0)),
}
ORDER = ["pelvis", "neck", "arm_r", "arm_l", "leg_r", "leg_l"]
LIMBS = ["arm_r", "arm_l", "leg_r", "leg_l"]
HEAD_RADIUS = 0.65  # studs; the rendered head is a rounded cylinder
HEAD_HEIGHT = 1.22
NECK_GAP = 0.04        # studs between the torso top and the head
EYE_ABOVE_NECK = 0.62  # studs; eye line used for standing eye height
HAND_AT = 0.775        # fraction of the arm length from the shoulder pivot to the hand point


class Body:
    """Dimensions of one character at a given scale."""

    def __init__(self, scale=1.0, build=1.0):
        self.k = scale
        self.s = STUD * scale
        self.build = build  # torso width multiplier for heavier or slimmer builds
        # Every dimension comes from PARTS/JOINTS, so a different body is a change to those
        # tables only (see Agent_Docs/06-style-seam.md).
        hip = JOINTS["pelvis"][1][2]
        torso = JOINTS["neck"][1][2]
        self.leg = PARTS["leg_r"][2][2] * self.s
        self.arm = PARTS["arm_r"][2][2] * HAND_AT * self.s  # shoulder pivot to the bottom of the hand
        self.hip_height = hip * self.s
        self.height = (hip + torso + NECK_GAP + HEAD_HEIGHT) * self.s
        self.eye_height = (hip + torso + NECK_GAP + EYE_ABOVE_NECK) * self.s
        self.lateral = abs(JOINTS["leg_l"][1][0]) * self.s * build
        self.half_depth = PARTS["torso"][2][1] / 2 * self.s
        self.head_radius = HEAD_RADIUS * self.s

    def joint_offset(self, joint):
        parent, off = JOINTS[joint]
        x, y, z = off
        if joint in ("arm_r", "arm_l"):
            x *= self.build
        if joint in ("leg_r", "leg_l"):
            x *= self.build
        return parent, (x * self.s, y * self.s, z * self.s)

    def part(self, name):
        joint, centre, size = PARTS[name]
        sx = size[0] * (self.build if name == "torso" else 1.0)
        return joint, tuple(c * self.s for c in centre), (sx * self.s, size[1] * self.s, size[2] * self.s)


def joint_matrices(body, pose):
    """World matrices for root and each joint.

    pose: dict with root (x, y, z), lean (rx, ry), yaw, pelvis (x, y, z) local offset,
    pelvis_rot, and per-joint euler rotations for neck, arm_r, arm_l, leg_r, leg_l.
    """
    rx, ry = pose.get("lean", (0.0, 0.0))
    mats = {"root": compose(pose["root"], (rx, ry, pose["yaw"]))}
    for j in ORDER:
        parent, off = body.joint_offset(j)
        if j == "pelvis":
            px, py, pz = pose.get("pelvis", (0.0, 0.0, body.hip_height))
            local = compose((px, py, pz), pose.get("pelvis_rot", (0.0, 0.0, 0.0)))
        else:
            local = compose(off, pose.get(j, (0.0, 0.0, 0.0)))
        mats[j] = mat_mul(mats[parent], local)
    return mats


class OBB:
    __slots__ = ("name", "centre", "axes", "half")

    def __init__(self, name, centre, ax, half):
        self.name, self.centre, self.axes, self.half = name, centre, ax, half

    def corners(self):
        out = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    out.append(tuple(
                        self.centre[i]
                        + self.axes[0][i] * self.half[0] * sx
                        + self.axes[1][i] * self.half[1] * sy
                        + self.axes[2][i] * self.half[2] * sz
                        for i in range(3)
                    ))
        return out

    def aabb(self):
        c = self.corners()
        return tuple(min(p[i] for p in c) for i in range(3)), tuple(max(p[i] for p in c) for i in range(3))

    def lowest(self):
        return min(p[2] for p in self.corners())


def part_boxes(body, mats, shrink=0.0):
    out = {}
    for name in PARTS:
        joint, centre, size = body.part(name)
        m = mats[joint]
        c = transform(m, centre)
        ax, _ = axes(m)
        out[name] = OBB(name, c, ax, tuple(max(0.001, s / 2 - shrink) for s in size))
    return out


def points(body, mats):
    """Named world points used by acting, cameras and QC."""
    s = body.s
    head = mats["neck"]
    return {
        "head": transform(head, (0, 0, 0.66 * s)),
        "eyes": transform(head, (0, -0.6 * s, 0.70 * s)),
        "face": transform(head, (0, -0.66 * s, 0.62 * s)),
        "top": transform(head, (0, 0, 1.35 * s)),
        "chest": transform(mats["pelvis"], (0, -0.5 * s, 1.45 * s)),
        "hips": transform(mats["pelvis"], (0, 0, 0.1 * s)),
        "shoulder_r": transform(mats["arm_r"], (0, 0, 0.3 * s)),
        "shoulder_l": transform(mats["arm_l"], (0, 0, 0.3 * s)),
        "hand_r": transform(mats["arm_r"], (0, 0, -body.arm)),
        "hand_l": transform(mats["arm_l"], (0, 0, -body.arm)),
        "foot_r": transform(mats["leg_r"], (0, 0, -body.leg)),
        "foot_l": transform(mats["leg_l"], (0, 0, -body.leg)),
        "face_dir": rotate(head, (0, -1, 0)),
    }


def aim_euler(direction):
    """Euler (x, y, 0) that points a limb's local -Z axis along `direction` (parent space)."""
    dx, dy, dz = direction
    n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    dx, dy, dz = dx / n, dy / n, dz / n
    a = math.asin(max(-1.0, min(1.0, dy)))
    b = math.atan2(-dx, -dz) if abs(math.cos(a)) > 1e-6 else 0.0
    return (a, b, 0.0)


def to_local(m, p):
    """World point to the local space of a rigid (unscaled) matrix."""
    d = v_sub(p, (m[0][3], m[1][3], m[2][3]))
    return (
        m[0][0] * d[0] + m[1][0] * d[1] + m[2][0] * d[2],
        m[0][1] * d[0] + m[1][1] * d[1] + m[2][1] * d[2],
        m[0][2] * d[0] + m[1][2] * d[1] + m[2][2] * d[2],
    )


def dist(a, b):
    return v_len(v_sub(a, b))
