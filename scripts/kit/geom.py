"""Oriented-box geometry for collision, occlusion and nav. Pure Python."""

import math

from .rig import OBB
from .mathx import euler_matrix, v_sub, v_dot


def element_obb(e, offset=(0, 0, 0)):
    """World OBB for a set element, or None for non-volumetric shapes."""
    shape = e["shape"]
    x, y, z = e["at"]
    ox, oy, oz = offset
    if shape == "box":
        w, d, h = e["size"]
    elif shape in ("cyl", "cone"):
        r = e["size"][0]
        w, d, h = 2 * r, 2 * r, e["size"][2]
    elif shape == "sphere":
        r = e["size"]
        w = d = h = 2 * r
    else:
        return None
    rx = math.radians(e.get("rx", 0.0))
    ry = math.radians(e.get("ry", 0.0))
    rz = math.radians(e.get("rot", 0.0))
    m = euler_matrix(rx, ry, rz)
    ax = [(m[0][i], m[1][i], m[2][i]) for i in range(3)]
    return OBB(e["name"], (x + ox, y + oy, z + oz), ax, (w / 2, d / 2, h / 2))


def overlap_depth(a, b):
    """Penetration depth of two OBBs by the separating axis theorem; 0 when separated."""
    axes_ = list(a.axes) + list(b.axes)
    for i in range(3):
        for j in range(3):
            c = (
                a.axes[i][1] * b.axes[j][2] - a.axes[i][2] * b.axes[j][1],
                a.axes[i][2] * b.axes[j][0] - a.axes[i][0] * b.axes[j][2],
                a.axes[i][0] * b.axes[j][1] - a.axes[i][1] * b.axes[j][0],
            )
            n = math.sqrt(v_dot(c, c))
            if n > 1e-6:
                axes_.append((c[0] / n, c[1] / n, c[2] / n))
    d = v_sub(b.centre, a.centre)
    best = float("inf")
    for ax in axes_:
        ra = sum(a.half[k] * abs(v_dot(a.axes[k], ax)) for k in range(3))
        rb = sum(b.half[k] * abs(v_dot(b.axes[k], ax)) for k in range(3))
        dist = abs(v_dot(d, ax))
        o = ra + rb - dist
        if o <= 0:
            return 0.0
        best = min(best, o)
    return best


def aabb_overlap(a, b, margin=0.0):
    (a0, a1), (b0, b1) = a, b
    return all(a0[i] - margin <= b1[i] and b0[i] - margin <= a1[i] for i in range(3))


def point_in_obb(p, box, margin=0.0):
    d = v_sub(p, box.centre)
    return all(abs(v_dot(d, box.axes[i])) <= box.half[i] + margin for i in range(3))


def segment_hits_obb(p0, p1, box, shrink=0.0):
    """True if the segment p0->p1 passes through the box (slab test in box space)."""
    d0 = v_sub(p0, box.centre)
    d1 = v_sub(p1, box.centre)
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        a = v_dot(d0, box.axes[i])
        b = v_dot(d1, box.axes[i])
        h = box.half[i] - shrink
        if h <= 0:
            return False
        dv = b - a
        if abs(dv) < 1e-12:
            if abs(a) > h:
                return False
            continue
        t0 = (-h - a) / dv
        t1 = (h - a) / dv
        if t0 > t1:
            t0, t1 = t1, t0
        tmin = max(tmin, t0)
        tmax = min(tmax, t1)
        if tmin > tmax:
            return False
    return True


def footprint(box):
    """2-D rectangle corners (xy) of an OBB footprint for nav."""
    c = box.centre
    ax, ay = box.axes[0], box.axes[1]
    hx, hy = box.half[0], box.half[1]
    return [(c[0] + ax[0] * sx * hx + ay[0] * sy * hy, c[1] + ax[1] * sx * hx + ay[1] * sy * hy)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def point_in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xi > x:
                inside = not inside
    return inside


class Nav:
    """Grid A* over a set's walkable rectangle, with obstacles inflated by an actor radius."""

    def __init__(self, set_, boxes, radius=0.45, cell=0.1):
        self.cell = cell
        x0, y0, x1, y1 = set_.bounds
        ox, oy, _ = set_.offset
        self.x0, self.y0 = x0 + ox, y0 + oy
        self.nx = int((x1 - x0) / cell) + 1
        self.ny = int((y1 - y0) / cell) + 1
        polys = []
        for b in boxes:
            if b.centre[2] - b.half[2] > 1.6 or b.centre[2] + b.half[2] < 0.05:
                continue  # overhead or flat
            polys.append(footprint(OBB(b.name, b.centre, b.axes, (b.half[0] + radius, b.half[1] + radius, b.half[2]))))
        self.blocked = bytearray(self.nx * self.ny)
        for j in range(self.ny):
            for i in range(self.nx):
                p = (self.x0 + i * cell, self.y0 + j * cell)
                for poly in polys:
                    if point_in_poly(p, poly):
                        self.blocked[j * self.nx + i] = 1
                        break

    def _idx(self, p):
        return (int(round((p[0] - self.x0) / self.cell)), int(round((p[1] - self.y0) / self.cell)))

    def free(self, p):
        i, j = self._idx(p)
        return 0 <= i < self.nx and 0 <= j < self.ny and not self.blocked[j * self.nx + i]

    def _nearest_free(self, ij):
        i0, j0 = ij
        for r in range(0, 40):
            for dj in range(-r, r + 1):
                for di in range(-r, r + 1):
                    if max(abs(di), abs(dj)) != r:
                        continue
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < self.nx and 0 <= j < self.ny and not self.blocked[j * self.nx + i]:
                        return (i, j)
        return ij

    def route(self, a, b):
        """Shortest smoothed path from a to b (world xy). Endpoints are kept exactly."""
        import heapq
        s = self._nearest_free(self._idx(a))
        g = self._nearest_free(self._idx(b))
        if self._line_free(a, b):
            return [tuple(b)]
        openq = [(0.0, s)]
        came = {s: None}
        cost = {s: 0.0}
        nbrs = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
        while openq:
            _, cur = heapq.heappop(openq)
            if cur == g:
                break
            for di, dj, c in nbrs:
                n = (cur[0] + di, cur[1] + dj)
                if not (0 <= n[0] < self.nx and 0 <= n[1] < self.ny) or self.blocked[n[1] * self.nx + n[0]]:
                    continue
                nc = cost[cur] + c
                if nc < cost.get(n, 1e18):
                    cost[n] = nc
                    came[n] = cur
                    h = math.hypot(g[0] - n[0], g[1] - n[1])
                    heapq.heappush(openq, (nc + h, n))
        if g not in came:
            return [tuple(b)]
        cells = []
        cur = g
        while cur is not None:
            cells.append(cur)
            cur = came[cur]
        cells.reverse()
        pts = [(self.x0 + i * self.cell, self.y0 + j * self.cell) for i, j in cells]
        pts[0] = tuple(a)
        pts[-1] = tuple(b)
        # String-pull
        out = [pts[0]]
        k = 0
        while k < len(pts) - 1:
            nxt = len(pts) - 1
            while nxt > k + 1 and not self._line_free(pts[k], pts[nxt]):
                nxt -= 1
            out.append(pts[nxt])
            k = nxt
        return out[1:]

    def _line_free(self, a, b):
        n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / (self.cell * 0.5)) + 1
        for i in range(1, n):
            t = i / n
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if not self.free(p):
                return False
        return True
