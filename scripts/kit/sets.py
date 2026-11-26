"""Location library. Each set is data: geometry elements, marks, seats, lights and sky.

The same element list drives the Blender build (kit/blender_build.py) and the
layout QC (collision, walking routes, camera occlusion), so what QC measures is
what renders. Coordinates are metres in the set's local frame, floor at z=0.
Characters face -Y at yaw 0; cameras usually look from -Y toward +Y.

Element keys:
  shape   box | cyl | cone | sphere | text | plane
  at      centre (x, y, z); for boxes z is the centre height
  size    (w, d, h) for box/plane, (radius, -, h) for cyl/cone, radius for sphere
  rot     z rotation in degrees (boxes may also give rx, ry)
  mat     material spec "kind:#hex[:extra]" (see blender_build.MATERIALS)
  solid   collides with actors (default True for boxes, False for text/plane)
  nav     blocks walking routes (default = solid)
  cut     a "wild" wall hidden when it sits between a shot camera and the action:
          give the inward normal, e.g. (0, 1) for a front wall at -Y
  bevel   bevel width in metres
  tag     free label (e.g. 'seat', 'bed', 'window')

Set attributes for sound: `surface` (footsteps) and `reverb` (IR seconds, wet) for dialogue.
"""

import math

from .mathx import hash01


class Set:
    def __init__(self, sid, title, kind="interior"):
        self.id, self.title, self.kind = sid, title, kind
        self.elements = []
        self.marks = {}        # name -> (x, y, yaw_deg)
        self.seats = {}        # name -> dict(x, y, z, yaw_deg, kind)
        self.points = {}       # name -> (x, y, z) for looks/inserts
        self.lights = []
        self.bounds = (-10, -10, 10, 10)  # walkable xy rectangle
        self.camera_bounds = None         # optional xy rectangle the camera must stay within
        self.times = {}        # time-of-day presets: name -> dict(world, sun, exposure)
        self.default_time = "day"
        self.offset = (0.0, 0.0, 0.0)     # world placement, set by the layout
        self.surface = "wood"             # footstep sound: concrete | wood | vinyl
        self.reverb = (0.6, 0.08)         # room for on-screen dialogue: (IR seconds, wet)

    # ---------------------------------------------------------------- builders
    def add(self, name, shape="box", at=(0, 0, 0), size=(1, 1, 1), mat="plastic:#888888", **kw):
        solid = kw.pop("solid", shape in ("box", "cyl", "cone", "sphere"))
        e = dict(name=name, shape=shape, at=tuple(at), size=size, mat=mat, solid=solid, **kw)
        e.setdefault("nav", solid)
        self.elements.append(e)
        return e

    def box(self, name, x0, y0, z0, x1, y1, z1, mat, **kw):
        """Axis-aligned box from two corners."""
        return self.add(name, "box", ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                        (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)), mat, **kw)

    def mark(self, name, x, y, yaw=0.0):
        self.marks[name] = (x, y, yaw)

    def seat(self, name, x, y, z, yaw=0.0, kind="chair"):
        self.seats[name] = dict(x=x, y=y, z=z, yaw=yaw, kind=kind)

    def light(self, **kw):
        self.lights.append(kw)

    def wall_with_openings(self, name, axis, fixed, a0, a1, height, thickness, mat, openings=(), cut=None, base=0.0):
        """A straight wall along X (axis='x', at y=fixed) or Y (axis='y', at x=fixed).

        openings: list of (start, end, bottom, top) along the wall for doors and windows.
        Builds the solid pieces around each opening so actors and cameras see real gaps.
        """
        pieces = []
        cuts = sorted(openings)
        cur = a0
        for (s, e, bot, top) in cuts:
            if s > cur:
                pieces.append((cur, s, base, height))
            if bot > base:
                pieces.append((s, e, base, bot))
            if top < height:
                pieces.append((s, e, top, height))
            cur = e
        if cur < a1:
            pieces.append((cur, a1, base, height))
        t2 = thickness / 2
        for i, (s, e, z0, z1) in enumerate(pieces):
            if axis == "x":
                self.box(f"{name}.{i}", s, fixed - t2, z0, e, fixed + t2, z1, mat, cut=cut, tag="wall")
            else:
                self.box(f"{name}.{i}", fixed - t2, s, z0, fixed + t2, e, z1, mat, cut=cut, tag="wall")

    # ---------------------------------------------------------------- queries
    def solids(self):
        return [e for e in self.elements if e.get("solid")]

    def world(self, p):
        ox, oy, oz = self.offset
        if len(p) == 2:
            return (p[0] + ox, p[1] + oy)
        return (p[0] + ox, p[1] + oy, p[2] + oz)


# ============================================================ reusable dressing

def tree(s, name, x, y, h=4.2, r=1.5, leaf="leaf:#4E8B3A", seed=0):
    s.add(name + ".trunk", "cyl", (x, y, h * 0.3), (0.18, 0, h * 0.6), "bark:#6B4A32", nav=True)
    for i in range(5):
        a = hash01(seed, i) * math.tau
        rr = r * (0.55 + 0.3 * hash01(seed, i, 1))
        off = r * 0.45 * hash01(seed, i, 2)
        s.add(f"{name}.leaf{i}", "box",
              (x + math.cos(a) * off, y + math.sin(a) * off, h * (0.68 + 0.18 * hash01(seed, i, 3))),
              (rr * 1.5, rr * 1.5, rr * 1.15), leaf, rot=hash01(seed, i, 4) * 90, bevel=0.18, solid=False, nav=False)


def bush(s, name, x, y, w=1.4, h=0.8, leaf="leaf:#3F7A33", seed=0):
    s.add(name, "box", (x, y, h / 2), (w, w * 0.7, h), leaf, bevel=0.15, rot=hash01(seed) * 20)


def window(s, name, axis, fixed, a, z0, w, h, outside="window_day:#9CC9EE", frame="paint:#F2EEE6", depth=0.12):
    """Window frame + glass/backdrop inside a wall opening (opening built by the wall)."""
    if axis == "x":
        s.add(name + ".view", "plane", (a, fixed + depth * 0.6, z0 + h / 2), (w, 0.01, h), outside, solid=False, face="y")
        for i, (dx, dz, sw, sh) in enumerate([(0, 0, w + 0.12, 0.08), (0, h, w + 0.12, 0.08), (-w / 2, h / 2, 0.08, h), (w / 2, h / 2, 0.08, h), (0, h / 2, 0.05, h), (0, h * 0.55, w, 0.05)]):
            s.add(f"{name}.frame{i}", "box", (a + dx, fixed, z0 + dz), (sw, depth, sh), frame, solid=False)
        s.add(name + ".sill", "box", (a, fixed - depth * 0.6, z0 - 0.03), (w + 0.25, 0.16, 0.05), frame, solid=False)
    else:
        s.add(name + ".view", "plane", (fixed + depth * 0.6 * (1 if fixed > 0 else -1), a, z0 + h / 2), (0.01, w, h), outside, solid=False, face="x")
        for i, (dy, dz, sw, sh) in enumerate([(0, 0, w + 0.12, 0.08), (0, h, w + 0.12, 0.08), (-w / 2, h / 2, 0.08, h), (w / 2, h / 2, 0.08, h), (0, h / 2, 0.05, h)]):
            s.add(f"{name}.frame{i}", "box", (fixed, a + dy, z0 + dz), (depth, sw, sh), frame, solid=False)


def door(s, name, axis, fixed, a, w=0.95, h=2.1, mat="paint:#8A5A3B", frame="paint:#EFE8DA", open_deg=0, sign=1, number=None):
    """Door leaf hinged at one side of an opening, plus casing. open_deg swings it into the room."""
    t = 0.05
    if axis == "x":
        hinge = (a - w / 2, fixed)
        ang = math.radians(open_deg) * sign
        cx = hinge[0] + math.cos(ang) * w / 2
        cy = hinge[1] + math.sin(ang) * w / 2
        s.add(name + ".leaf", "box", (cx, cy, h / 2), (w, t, h), mat, rot=math.degrees(ang), bevel=0.01)
        s.add(name + ".knob", "sphere", (cx + math.cos(ang) * w * 0.38 - math.sin(ang) * 0.05, cy + math.sin(ang) * w * 0.38 + math.cos(ang) * 0.05 * -1, 1.0), 0.04, "metal:#C9B27C", solid=False)
        for i, (dx, sw, sh, z) in enumerate([(-w / 2 - 0.05, 0.1, h + 0.1, (h + 0.1) / 2), (w / 2 + 0.05, 0.1, h + 0.1, (h + 0.1) / 2), (0, w + 0.2, 0.1, h + 0.05)]):
            s.add(f"{name}.casing{i}", "box", (a + dx, fixed, z), (sw, 0.16, sh), frame, solid=False)
        if number:
            s.add(name + ".sign", "text", (a, fixed - 0.09, h + 0.25), 0.16, "paint:#2B3A4A", text=number, face="-y", solid=False)
    else:
        hinge = (fixed, a - w / 2)
        ang = math.radians(open_deg) * sign
        cx = hinge[0] - math.sin(ang) * w / 2
        cy = hinge[1] + math.cos(ang) * w / 2
        s.add(name + ".leaf", "box", (cx, cy, h / 2), (t, w, h), mat, rot=math.degrees(ang), bevel=0.01)
        for i, (dy, sw, sh, z) in enumerate([(-w / 2 - 0.05, 0.1, h + 0.1, (h + 0.1) / 2), (w / 2 + 0.05, 0.1, h + 0.1, (h + 0.1) / 2), (0, w + 0.2, 0.1, h + 0.05)]):
            s.add(f"{name}.casing{i}", "box", (fixed, a + dy, z), (0.16, sw, sh), frame, solid=False)


def chair(s, name, x, y, yaw=0, seat_h=0.46, mat="wood:#8A5B3C", w=0.5):
    """Simple chair facing yaw (degrees, 0 = facing -Y). Registers a seat."""
    a = math.radians(yaw)
    fx, fy = math.sin(a), -math.cos(a)
    s.add(name + ".seat", "box", (x, y, seat_h - 0.03), (w, w, 0.06), mat, rot=yaw, bevel=0.015, tag="seat")
    bx, by = x - fx * (w / 2 - 0.03), y - fy * (w / 2 - 0.03)
    s.add(name + ".back", "box", (bx, by, seat_h + 0.32), (w, 0.05, 0.58), mat, rot=yaw, bevel=0.015)
    for i, (lx, ly) in enumerate([(-1, -1), (1, -1), (-1, 1), (1, 1)]):
        px = x + math.cos(a) * lx * (w / 2 - 0.04) - math.sin(a) * ly * (w / 2 - 0.04)
        py = y + math.sin(a) * lx * (w / 2 - 0.04) + math.cos(a) * ly * (w / 2 - 0.04)
        s.add(f"{name}.leg{i}", "box", (px, py, (seat_h - 0.06) / 2), (0.05, 0.05, seat_h - 0.06), mat, rot=yaw, nav=True)
    s.seat(name, x + fx * 0.02, y + fy * 0.02, seat_h, yaw)


# ================================================================== locations

def school_gate():
    s = Set("school_gate", "School gate", "exterior")
    s.surface, s.reverb = "concrete", (0.6, 0.06)
    s.bounds = (-11, -7.5, 11, 11)
    s.default_time = "morning"
    s.times = {
        "morning": dict(sky=("#5FA8E8", "#CFE4F5"), strength=0.75, sun=dict(elev=34, azim=-35, color="#FFE7C4", power=3.4)),
        "afternoon": dict(sky=("#86BDEB", "#F6E7CF"), strength=1.0, sun=dict(elev=22, azim=40, color="#FFD9A8", power=4.0)),
    }
    # Ground: road, curb, sidewalk, schoolyard, grass verges
    s.box("road", -30, -16, -0.05, 30, -4.2, 0.0, "asphalt:#3C3F45", solid=False, nav=False)
    for i in range(-6, 7):
        s.box(f"lane{i}", i * 4 - 1, -10.15, 0.0, i * 4 + 1, -9.95, 0.006, "paint:#F2D24B", solid=False, nav=False)
    s.box("curb", -30, -4.35, 0.0, 30, -4.0, 0.12, "concrete:#B9B6AE", solid=False, nav=False)
    s.box("sidewalk", -30, -4.0, -0.05, 30, 0.0, 0.10, "pavers:#CFCAC0", solid=False, nav=False)
    s.box("yard", -30, 0.0, -0.05, 30, 9.0, 0.02, "concrete:#B8B3A8", solid=False, nav=False)
    s.box("grass_l", -30, 0.3, 0.0, -4.0, 8.6, 0.05, "grass:#6FA34A", solid=False, nav=False)
    s.box("grass_r", 4.0, 0.3, 0.0, 30, 8.6, 0.05, "grass:#6FA34A", solid=False, nav=False)
    # Fence with a gate opening at |x| < 1.6
    for side, (a0, a1) in (("l", (-14, -1.6)), ("r", (1.6, 14))):
        s.box(f"fence_rail_top_{side}", a0, -0.03, 1.12, a1, 0.03, 1.18, "metal:#2E4C3B")
        s.box(f"fence_rail_low_{side}", a0, -0.03, 0.18, a1, 0.03, 0.24, "metal:#2E4C3B")
        n = int((a1 - a0) / 0.22)
        for i in range(n + 1):
            x = a0 + i * (a1 - a0) / n
            s.box(f"fence_bar_{side}{i}", x - 0.018, -0.018, 0.0, x + 0.018, 0.018, 1.25, "metal:#2E4C3B")
    for side, x in (("l", -1.75), ("r", 1.75)):
        s.box(f"gate_post_{side}", x - 0.15, -0.15, 0.0, x + 0.15, 0.15, 1.75, "brick:#A4553F", bevel=0.02)
        s.box(f"gate_cap_{side}", x - 0.2, -0.2, 1.75, x + 0.2, 0.2, 1.85, "concrete:#D9D3C7")
    s.box("gate_arch", -1.9, -0.12, 2.35, 1.9, 0.12, 2.55, "metal:#2E4C3B", solid=False)
    s.add("gate_sign", "text", (0, -0.14, 2.62), 0.24, "paint:#F6F1E6", text="MAPLE HILL SCHOOL", face="-y", solid=False)
    for side, x in (("l", -1.78), ("r", 1.78)):
        s.box(f"arch_post_{side}", x - 0.05, -0.05, 1.85, x + 0.05, 0.05, 2.45, "metal:#2E4C3B", solid=False)
    # School building
    s.wall_with_openings("school_front", "x", 9.0, -12, 12, 6.6, 0.4, "brick:#B5563F",
                         openings=[(-1.0, 1.0, 0.0, 2.6)] + [(x - 0.8, x + 0.8, 1.0, 2.5) for x in (-9, -5.5, 4.0 + 1.5, 9)]
                         + [(x - 0.8, x + 0.8, 3.7, 5.3) for x in (-9, -5.5, -2.0, 2.0, 5.5, 9)])
    s.box("school_back", -12, 9.2, 0, 12, 11, 6.6, "paint:#3A3530", solid=False)
    s.box("school_roof", -12.4, 8.6, 6.6, 12.4, 11.2, 7.0, "concrete:#E4DED2")
    s.box("school_band", -12, 8.75, 3.2, 12, 8.82, 3.4, "concrete:#E4DED2", solid=False)
    for x in (-9, -5.5, 5.5, 9):
        window(s, f"win_low_{x}", "x", 8.8, x, 1.0, 1.6, 1.5, outside="window_class:#F2E2B8", frame="paint:#F5F2EA")
    for x in (-9, -5.5, -2.0, 2.0, 5.5, 9):
        window(s, f"win_up_{x}", "x", 8.8, x, 3.7, 1.6, 1.6, outside="window_class:#F2E2B8", frame="paint:#F5F2EA")
    s.add("school_door", "box", (0, 9.0, 1.3), (1.9, 0.1, 2.6), "paint:#2F5D8C", bevel=0.01)
    s.add("school_door_glass", "plane", (0, 8.94, 1.75), (1.4, 0.01, 0.9), "window_class:#F2E2B8", solid=False, face="y")
    s.box("school_steps", -1.6, 8.2, 0, 1.6, 8.8, 0.18, "concrete:#D9D3C7")
    s.add("school_name", "text", (0, 8.76, 3.0), 0.42, "paint:#F6F1E6", text="MAPLE HILL", face="-y", solid=False)
    # Flagpole, trees, bushes, bench, lamp, bike rack
    s.add("flagpole", "cyl", (-3.2, 6.0, 3.5), (0.05, 0, 7.0), "metal:#D6D6D6")
    s.box("flag", -3.15, 5.98, 5.8, -2.05, 6.02, 6.55, "fabric:#D94B3D", solid=False)
    tree(s, "tree_l", -6.5, 3.5, seed=1)
    tree(s, "tree_r", 6.8, 4.0, seed=2, leaf="leaf:#5E9A3C")
    tree(s, "tree_far", 11.5, 2.5, seed=3, h=4.8)
    tree(s, "street_tree", -8.5, -3.2, seed=4, h=4.6, r=1.4)
    bush(s, "bush_a", -3.0, 1.0, seed=1)
    bush(s, "bush_b", 3.2, 1.1, 1.2, seed=2)
    bush(s, "bush_c", -9.5, 1.2, seed=3)
    s.box("bench_seat", 4.6, -1.0, 0.42, 6.4, -0.55, 0.48, "wood:#9A6A45", bevel=0.01, tag="seat")
    s.box("bench_back", 4.6, -0.5, 0.55, 6.4, -0.44, 0.95, "wood:#9A6A45", bevel=0.01)
    for x in (4.75, 6.25):
        s.box(f"bench_leg{x}", x - 0.04, -1.0, 0.0, x + 0.04, -0.45, 0.42, "metal:#2E2E2E")
    s.add("lamp_post", "cyl", (8.2, -3.5, 2.2), (0.07, 0, 4.4), "metal:#2E3B36")
    s.add("lamp_head", "box", (8.2, -3.5, 4.45), (0.45, 0.25, 0.2), "metal:#2E3B36", solid=False)
    # Parked car on the road
    s.box("car_body", -7.2, -6.0, 0.25, -3.0, -4.6, 1.0, "carpaint:#3D6EA8", bevel=0.12)
    s.box("car_cabin", -6.3, -5.9, 1.0, -3.9, -4.7, 1.62, "carpaint:#3D6EA8", bevel=0.12)
    s.add("car_glass_side", "plane", (-5.1, -4.69, 1.32), (2.2, 0.01, 0.48), "glass:#22303A", solid=False, face="-y")
    for i, x in enumerate((-6.4, -3.8)):
        s.add(f"car_wheel{i}", "cyl", (x, -4.58, 0.33), (0.33, 0, 0.24), "rubber:#1E1E1E", rx=90)
    # Houses across the street (backdrop, rarely seen)
    for i, x in enumerate((-12, -4, 4, 12)):
        col = ["#E9D8B8", "#C9DCE6", "#EED1C4", "#D6E3C3"][i]
        s.box(f"house{i}", x - 3, -24, 0, x + 3, -19, 4.2, "wall:" + col, solid=False, nav=False)
        s.add(f"house_roof{i}", "box", (x, -21.5, 4.6), (6.6, 5.6, 0.9), "roof:#6E4B3E", solid=False, nav=False)

    s.mark("yard_far", 0, 6.5, 0)
    s.mark("yard_mid", 0.3, 3.2, 0)
    s.mark("gate", 0, 0.0, 0)
    s.mark("parent_a", -3.0, -1.6, 20)
    s.mark("child_a_start", -1.2, 6.0, 160)
    s.mark("mom_wait", 1.9, -2.0, 0)
    s.mark("kai_exit", 0.6, 1.2, 0)
    s.mark("kai_by_mom", 1.9, -1.15, 180)
    s.points["gate_top"] = (0, 0, 2.6)
    return s


def grandpa_living():
    s = Set("grandpa_living", "Grandpa's living room")
    s.surface, s.reverb = "wood", (0.5, 0.1)
    s.bounds = (-3.0, -2.6, 3.0, 2.4)
    s.default_time = "afternoon"
    s.times = {"afternoon": dict(sky=("#F1C38A", "#F7DDB8"), strength=0.35, sun=dict(elev=18, azim=160, color="#FFC98A", power=5.5, interior=True))}
    H = 2.8
    s.box("floor", -3.2, -3.2, -0.1, 3.2, 2.7, 0.0, "floor_wood:#8B5E3C", solid=False, nav=False)
    s.box("ceiling", -3.2, -3.2, H, 3.2, 2.7, H + 0.1, "paint:#EFE6D6", solid=False, nav=False, cut=(0, 0, -1))
    s.wall_with_openings("wall_back", "x", 2.6, -3.2, 3.2, H, 0.2, "wallpaper:#C9B48A",
                         openings=[(0.9, 2.5, 0.9, 2.3)])
    s.wall_with_openings("wall_left", "y", -3.1, -3.2, 2.7, H, 0.2, "wallpaper:#C9B48A",
                         openings=[(-1.85, -0.35, 0.0, 2.3)], cut=(1, 0))
    s.wall_with_openings("wall_right", "y", 3.1, -3.2, 2.7, H, 0.2, "wallpaper:#C9B48A", cut=(-1, 0))
    s.wall_with_openings("wall_front", "x", -3.1, -3.2, 3.2, H, 0.2, "wallpaper:#C9B48A", cut=(0, 1))
    # wainscot and skirting
    s.box("wainscot_back", -3.0, 2.46, 0, 3.0, 2.5, 0.9, "wood:#6B4630", solid=False)
    s.box("wainscot_left", -3.0, -3.0, 0, -2.96, 2.5, 0.9, "wood:#6B4630", solid=False)
    window(s, "window", "x", 2.6, 1.7, 0.9, 1.6, 1.4, outside="window_day:#F4D7A6", frame="paint:#F3EEE3")
    s.box("curtain_l", 0.62, 2.38, 0.6, 0.92, 2.48, 2.5, "fabric:#7C2F2B", bevel=0.04, solid=False)
    s.box("curtain_r", 2.48, 2.38, 0.6, 2.78, 2.48, 2.5, "fabric:#7C2F2B", bevel=0.04, solid=False)
    door(s, "door", "y", -3.1, -1.1, w=1.4, h=2.25, open_deg=0)
    # Armchair facing -Y (towards the TV / camera side)
    ax, ay = -0.4, 1.35
    s.box("armchair_base", ax - 0.6, ay - 0.42, 0.0, ax + 0.6, ay + 0.45, 0.42, "fabric:#5C7A6A", bevel=0.06)
    s.box("armchair_cushion", ax - 0.42, ay - 0.42, 0.42, ax + 0.42, ay + 0.3, 0.5, "fabric:#6A8B79", bevel=0.05, tag="seat")
    s.box("armchair_back", ax - 0.6, ay + 0.3, 0.42, ax + 0.6, ay + 0.55, 1.25, "fabric:#5C7A6A", bevel=0.07)
    s.box("armchair_arm_l", ax - 0.6, ay - 0.42, 0.42, ax - 0.48, ay + 0.3, 0.62, "fabric:#5C7A6A", bevel=0.05)
    s.box("armchair_arm_r", ax + 0.48, ay - 0.42, 0.42, ax + 0.6, ay + 0.3, 0.62, "fabric:#5C7A6A", bevel=0.05)
    s.seat("armchair", ax, ay - 0.04, 0.5, 0)
    # Side table, lamp, photo frame
    tx, ty = 0.75, 1.55
    s.box("side_table", tx - 0.28, ty - 0.28, 0.0, tx + 0.28, ty + 0.28, 0.6, "wood:#7A4E32", bevel=0.02)
    s.add("lamp_base", "cyl", (tx + 0.08, ty + 0.1, 0.72), (0.07, 0, 0.24), "ceramic:#E8DCC2", solid=False)
    s.add("lamp_shade", "cone", (tx + 0.08, ty + 0.1, 0.98), (0.2, 0.12, 0.26), "lampshade:#F4E3BE", solid=False)
    s.add("photo_frame", "box", (tx - 0.1, ty - 0.08, 0.72), (0.22, 0.03, 0.26), "wood:#3E2A1E", rot=-12, rx=-10, solid=False)
    s.add("photo", "plane", (tx - 0.1 + 0.002, ty - 0.096, 0.72), (0.17, 0.01, 0.2), "photo:grandma", rot=-12, solid=False, face="-y")
    s.points["photo"] = (tx - 0.1, ty - 0.1, 0.72)
    s.light(type="point", at=(tx + 0.08, ty + 0.1, 0.98), color="#FFC27A", power=35, radius=0.1)
    # Rug, bookshelf, sideboard, clock, pictures, plant
    s.box("rug", -1.8, -1.2, 0.0, 1.4, 1.0, 0.012, "rug:#8C3B32", solid=False, nav=False)
    s.box("bookshelf", -2.98, 0.6, 0, -2.6, 2.2, 2.1, "wood:#5A3B28", bevel=0.01)
    for i in range(4):
        for j in range(7):
            col = ["#7D3C2E", "#2F5466", "#C9A35B", "#4E6B3A", "#8A6F9B", "#B5553F", "#D8CBB0"][(i * 3 + j) % 7]
            s.add(f"book{i}_{j}", "box", (-2.66, 0.75 + j * 0.2, 0.25 + i * 0.5), (0.06, 0.12 + 0.03 * ((i + j) % 3), 0.26 + 0.04 * ((i * j) % 3)), "plastic:" + col, solid=False)
    s.add("clock", "cyl", (-1.5, 2.48, 2.1), (0.2, 0, 0.05), "ceramic:#F3EBDA", rx=90, solid=False)
    s.add("clock_rim", "cyl", (-1.5, 2.49, 2.1), (0.23, 0, 0.04), "wood:#3E2A1E", rx=90, solid=False)
    for i, (x, z, w, h, c) in enumerate([(-0.4, 1.9, 0.5, 0.38, "photo:landscape"), (-2.3, 1.75, 0.35, 0.45, "photo:portrait")]):
        s.add(f"picture{i}", "box", (x, 2.48, z), (w + 0.06, 0.03, h + 0.06), "wood:#4A3222", solid=False)
        s.add(f"picture{i}.img", "plane", (x, 2.46, z), (w, 0.01, h), c, solid=False, face="-y")
    s.add("plant_pot", "cyl", (2.45, 1.95, 0.22), (0.2, 0, 0.44), "ceramic:#B86A45")
    for i in range(6):
        a = i * 1.05
        s.add(f"plant_leaf{i}", "box", (2.45 + 0.13 * math.cos(a), 1.95 + 0.13 * math.sin(a), 0.62 + 0.06 * (i % 3)), (0.12, 0.34, 0.03), "leaf:#4F7F3A", rot=math.degrees(a), rx=35, solid=False)
    s.box("sideboard", 1.5, -2.95, 0, 2.9, -2.5, 0.8, "wood:#6B4630", bevel=0.02)
    s.box("tv", 1.8, -2.75, 0.8, 2.6, -2.68, 1.3, "plastic:#1B1B1E", bevel=0.01)
    s.mark("door_in", -2.6, -1.1, 90)
    s.mark("near_chair", 0.4, 0.35, 160)
    s.mark("by_table", 0.95, 0.75, -20)
    s.mark("center", -0.3, -0.3, 0)
    s.light(type="area", at=(0, -0.5, H - 0.05), rot=(0, 0, 0), size=2.0, color="#FFE2BC", power=120)
    return s


def hospital_room():
    s = Set("hospital_room", "Hospital room")
    s.surface, s.reverb = "vinyl", (0.7, 0.12)
    s.bounds = (-2.7, -2.6, 2.7, 2.4)
    s.default_time = "night"
    s.times = {"night": dict(sky=("#1A2A4F", "#2C4270"), strength=0.6, sun=None)}
    H = 2.8
    s.box("floor", -3, -3, -0.1, 3, 2.7, 0.0, "floor_vinyl:#B9C2C4", solid=False, nav=False)
    s.box("ceiling", -3, -3, H, 3, 2.7, H + 0.1, "ceiling_tile:#E6EAEA", solid=False, nav=False, cut=(0, 0, -1))
    s.wall_with_openings("wall_back", "x", 2.6, -3, 3, H, 0.2, "wall:#A9C3C4", openings=[(-2.2, -0.6, 0.9, 2.3)])
    s.wall_with_openings("wall_left", "y", -2.9, -3, 2.7, H, 0.2, "wall:#A9C3C4", cut=(1, 0))
    s.wall_with_openings("wall_right", "y", 2.9, -3, 2.7, H, 0.2, "wall:#A9C3C4", openings=[(-2.2, -0.8, 0.0, 2.3)], cut=(-1, 0))
    s.wall_with_openings("wall_front", "x", -2.9, -3, 3, H, 0.2, "wall:#A9C3C4", cut=(0, 1))
    s.box("rail_back", -2.8, 2.47, 0.85, 2.8, 2.5, 0.95, "wood:#C7B08A", solid=False)
    window(s, "window", "x", 2.6, -1.4, 0.9, 1.6, 1.4, outside="window_night:#1B2A4C", frame="paint:#E8ECEC")
    s.light(type="area", at=(-1.4, 2.3, 1.6), rot=(-90, 0, 0), size=1.4, color="#7E9BD6", power=70)
    door(s, "door", "y", 2.9, -1.5, w=1.35, h=2.25, mat="paint:#D7DEE0", open_deg=0)
    # Bed: length along X, head at +X? Keep head at +Y against the back wall, bed spans y -0.6..1.9, x -0.5..0.5
    bx0, bx1, by0, by1 = 0.1, 1.15, -0.4, 2.0
    s.box("bed_frame", bx0 - 0.04, by0, 0.25, bx1 + 0.04, by1, 0.48, "metal:#D9DEE2", bevel=0.02)
    s.box("bed_mattress", bx0, by0 + 0.02, 0.48, bx1, by1 - 0.05, 0.64, "fabric:#F1F3F4", bevel=0.04, tag="bed")
    s.box("bed_blanket", bx0 - 0.02, by0 - 0.01, 0.6, bx1 + 0.02, by0 + 1.35, 0.68, "fabric:#7FA6C9", bevel=0.03, solid=False)
    s.box("bed_pillow", bx0 + 0.15, by1 - 0.55, 0.64, bx1 - 0.15, by1 - 0.12, 0.78, "fabric:#FFFFFF", bevel=0.06, solid=False)
    s.box("bed_head", bx0 - 0.05, by1 - 0.05, 0.25, bx1 + 0.05, by1 + 0.03, 1.15, "metal:#C7CDD2", bevel=0.02)
    s.box("bed_foot", bx0 - 0.05, by0 - 0.06, 0.25, bx1 + 0.05, by0 + 0.02, 0.85, "metal:#C7CDD2", bevel=0.02)
    for i, (x, y) in enumerate([(bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)]):
        s.box(f"bed_leg{i}", x - 0.03, y - 0.03, 0.0, x + 0.03, y + 0.03, 0.25, "metal:#9AA3AA")
    # Grandpa sits on the bed's left edge facing -X toward the room.
    s.seat("bed_edge", bx0 + 0.28, 0.95, 0.64, -90, kind="chair")
    # Monitor and IV
    s.add("monitor_pole", "cyl", (1.55, 2.15, 0.75), (0.035, 0, 1.5), "metal:#B6BEC4")
    s.box("monitor", 1.32, 1.98, 1.4, 1.78, 2.2, 1.75, "plastic:#E3E6E8", bevel=0.02)
    s.add("monitor_screen", "plane", (1.55, 1.97, 1.58), (0.38, 0.01, 0.28), "monitor:heart", solid=False, face="-y")
    s.light(type="point", at=(1.55, 1.8, 1.58), color="#7CF5B0", power=6, radius=0.05)
    s.add("iv_pole", "cyl", (-0.25, 2.1, 0.95), (0.025, 0, 1.9), "metal:#B6BEC4")
    s.box("iv_bag", -0.36, 2.06, 1.55, -0.14, 2.12, 1.85, "glass:#DDEFF5", solid=False)
    # Bedside table with flowers and a lamp
    s.box("bedside", 1.35, 0.9, 0, 1.85, 1.4, 0.62, "wood:#C7B08A", bevel=0.02)
    s.add("vase", "cyl", (1.6, 1.15, 0.73), (0.06, 0, 0.22), "glass:#9FD3D6", solid=False)
    for i in range(5):
        a = i * 1.25
        s.add(f"flower{i}", "sphere", (1.6 + 0.07 * math.cos(a), 1.15 + 0.07 * math.sin(a), 0.92 + 0.03 * (i % 2)), 0.05, "plastic:" + ["#F2C14E", "#E86A5C", "#F2F2F2"][i % 3], solid=False)
    s.add("bed_lamp", "cone", (1.72, 1.3, 0.85), (0.12, 0.07, 0.16), "lampshade:#F7E4BC", solid=False)
    s.light(type="point", at=(1.72, 1.25, 0.88), color="#FFBE7A", power=60, radius=0.08)
    # Visitor chair and curtain rail
    chair(s, "visitor_chair", -1.7, 0.3, 90, mat="plastic:#5D7F96")
    s.box("curtain_rail", -0.6, -0.9, 2.6, 2.4, -0.86, 2.63, "metal:#C7CDD2", solid=False)
    s.box("curtain", 1.7, -0.95, 0.3, 2.4, -0.85, 2.58, "fabric:#9FC1B5", bevel=0.04, solid=False)
    s.light(type="area", at=(0.3, 0.4, H - 0.05), size=1.6, color="#D6E6FF", power=140)
    s.light(type="area", at=(-1.6, -1.2, H - 0.05), size=1.2, color="#C8DAFF", power=60)
    s.mark("door_in", 2.3, -1.5, -90)
    s.mark("bedside", -0.62, 0.95, 90)
    s.mark("hug_spot", -0.40, 0.95, 90)
    s.mark("mom_back", -1.2, -0.9, 60)
    s.points["monitor"] = (1.55, 1.97, 1.58)
    return s


def hospital_corridor():
    s = Set("hospital_corridor", "Hospital corridor")
    s.surface, s.reverb = "vinyl", (1.6, 0.18)
    s.bounds = (-8, -1.3, 8, 1.2)
    s.default_time = "night"
    s.times = {"night": dict(sky=("#1A2A4F", "#2C4270"), strength=0.45, sun=None)}
    H = 2.8
    s.box("floor", -9, -1.6, -0.1, 9, 1.6, 0.0, "floor_vinyl:#C6CCC8", solid=False, nav=False)
    s.box("ceiling", -9, -1.6, H, 9, 1.6, H + 0.1, "ceiling_tile:#E6EAEA", solid=False, nav=False, cut=(0, 0, -1))
    s.wall_with_openings("wall_back", "x", 1.6, -9, 9, H, 0.2, "wall:#B8CFCB", openings=[(-5.75, -4.25, 0.0, 2.3), (4.8, 6.6, 1.0, 2.3)])
    s.wall_with_openings("wall_front", "x", -1.6, -9, 9, H, 0.2, "wall:#B8CFCB", cut=(0, 1))
    s.wall_with_openings("wall_l", "y", -8.8, -1.6, 1.6, H, 0.2, "wall:#B8CFCB", cut=(1, 0))
    s.wall_with_openings("wall_r", "y", 8.8, -1.6, 1.6, H, 0.2, "wall:#B8CFCB", cut=(-1, 0))
    s.box("handrail", -9, 1.42, 0.88, 9, 1.48, 0.94, "wood:#C7B08A", solid=False)
    s.box("stripe", -9, 1.49, 0.3, 9, 1.5, 0.42, "paint:#5E9C93", solid=False)
    door(s, "door_room", "x", 1.6, -5.0, w=1.45, h=2.25, mat="paint:#D7DEE0", open_deg=0, number="ROOM 12")
    window(s, "win", "x", 1.6, 5.7, 1.0, 1.8, 1.3, outside="window_night:#1B2A4C", frame="paint:#E8ECEC")
    s.light(type="area", at=(5.7, 1.4, 1.65), rot=(-90, 0, 0), size=1.5, color="#6E8CCB", power=18)
    # Waiting chairs (row of 3) against the back wall
    for i, x in enumerate((0.85, 1.85, 2.85)):
        chair(s, f"wait{i}", x, 1.05, 0, seat_h=0.45, mat="plastic:#3F6E8C", w=0.52)
    s.box("chair_beam", 0.5, 1.2, 0.2, 3.2, 1.28, 0.28, "metal:#7A8590")
    s.box("vending", -2.6, 0.85, 0, -1.6, 1.5, 1.9, "plastic:#B23A3A", bevel=0.03)
    s.add("vending_glass", "plane", (-2.1, 0.84, 1.15), (0.7, 0.01, 1.1), "screen:#FCE9C6:1.2", solid=False, face="-y")
    s.box("cart", -7.2, 0.6, 0.0, -6.2, 1.3, 0.95, "metal:#C2C9CE", bevel=0.02)
    s.add("plant", "cyl", (3.5, 1.3, 0.25), (0.2, 0, 0.5), "ceramic:#8C8C8C")
    for i in range(5):
        a = i * 1.25
        s.add(f"plant_leaf{i}", "box", (3.5 + 0.12 * math.cos(a), 1.3 + 0.12 * math.sin(a), 0.75), (0.12, 0.4, 0.03), "leaf:#4F7F3A", rot=math.degrees(a), rx=40, solid=False)
    for i, x in enumerate(range(-8, 9, 3)):
        s.box(f"panel{i}", x - 0.6, -0.3, H - 0.04, x + 0.6, 0.3, H - 0.01, "emit:#EAF4FF:6", solid=False, nav=False)
        s.light(type="area", at=(x, 0, H - 0.06), size=1.0, color="#DCEBFF", power=55 if abs(x - 1) < 4 else 38)
    s.mark("room_door", -5.0, 0.7, 180)
    s.mark("kai_out", -5.0, 0.15, 90)
    s.mark("mid", -1.0, 0.0, 90)
    s.mark("mom_front", 1.85, 0.25, 0)
    s.seat("mom_seat", 1.85, 1.07, 0.45, 0)
    return s


LIBRARY = {
    "school_gate": school_gate,
    "grandpa_living": grandpa_living,
    "hospital_room": hospital_room,
    "hospital_corridor": hospital_corridor,
}


def load(sid):
    if sid not in LIBRARY:
        raise KeyError(f"Unknown set '{sid}'. Available: {sorted(LIBRARY)}")
    return LIBRARY[sid]()
