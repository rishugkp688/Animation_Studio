"""Small dependency-free vector and matrix helpers that match Blender's conventions.

Matrices are 4x4 row-major lists. Euler rotations are XYZ order in radians, like
Blender's default rotation mode, so a transform computed here equals
`Matrix.LocRotScale(loc, Euler(rot, 'XYZ'), scale)` inside Blender.
"""

import math

TAU = math.tau


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def smoother(t):
    t = clamp(t)
    return t * t * t * (t * (t * 6 - 15) + 10)


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in(t):
    t = clamp(t)
    return t ** 3


def overshoot(t, amount=1.4):
    """Back-ease out: reaches past 1 then settles; used for snappy pose hits."""
    t = clamp(t) - 1
    return 1 + t * t * ((amount + 1) * t + amount)


def angle_lerp(a, b, t):
    d = (b - a + math.pi) % TAU - math.pi
    return a + d * t


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_norm(a):
    n = v_len(a)
    return (0.0, 0.0, 0.0) if n < 1e-12 else (a[0] / n, a[1] / n, a[2] / n)


def v_lerp(a, b, t):
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def euler_matrix(rx, ry, rz):
    """3x3 rotation for Blender XYZ euler (R = Rz * Ry * Rx)."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def compose(loc, rot=(0, 0, 0), scale=1.0):
    r = euler_matrix(*rot)
    s = scale if isinstance(scale, (tuple, list)) else (scale, scale, scale)
    return [
        [r[0][0] * s[0], r[0][1] * s[1], r[0][2] * s[2], loc[0]],
        [r[1][0] * s[0], r[1][1] * s[1], r[1][2] * s[2], loc[1]],
        [r[2][0] * s[0], r[2][1] * s[1], r[2][2] * s[2], loc[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(m, p):
    return (
        m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
        m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
        m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3],
    )


def rotate(m, d):
    return (
        m[0][0] * d[0] + m[0][1] * d[1] + m[0][2] * d[2],
        m[1][0] * d[0] + m[1][1] * d[1] + m[1][2] * d[2],
        m[2][0] * d[0] + m[2][1] * d[1] + m[2][2] * d[2],
    )


def axes(m):
    """Unit local axes of a (possibly scaled) matrix and their scales."""
    cols = [(m[0][i], m[1][i], m[2][i]) for i in range(3)]
    scales = [v_len(c) for c in cols]
    return [v_norm(c) for c in cols], scales


def yaw_to(src, dst):
    """Yaw (around Z) that turns a character's -Y facing toward dst."""
    dx, dy = dst[0] - src[0], dst[1] - src[1]
    return math.atan2(dx, -dy)


def facing(yaw):
    """World direction of a character's front (-Y local) for a yaw."""
    return (math.sin(yaw), -math.cos(yaw), 0.0)


def hash01(*keys):
    """Deterministic pseudo-random value in [0, 1) from any hashable keys."""
    h = 2166136261
    for ch in repr(keys).encode():
        h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
    return (h % 100003) / 100003.0


def noise1(x, seed=0):
    """Smooth 1-D value noise in [-1, 1]."""
    i = math.floor(x)
    f = x - i
    a = hash01(seed, i) * 2 - 1
    b = hash01(seed, i + 1) * 2 - 1
    return lerp(a, b, smooth(f))
