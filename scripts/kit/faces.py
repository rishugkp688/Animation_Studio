"""Draw the shared face atlases (Roblox-style decal faces) with PIL.

Atlases are 4x4 cells; a cell covers the whole face decal (u across +-60 degrees of the
cylinder head, v over the head height). Index order matches acting.EYES / MOUTHS / EXTRAS.
Eye whites are pure white so the shader can use the red channel as a mask for the pupils.
"""

import math

from PIL import Image, ImageDraw, ImageFilter

from .acting import EYES, MOUTHS, EXTRAS

CELL = 512
SS = 2
AX = 0.894  # u units per v unit so circles stay round on the cylinder
INK = (34, 24, 26, 255)
WHITE = (255, 255, 255, 255)
MOUTH_IN = (112, 30, 38, 255)
TONGUE = (226, 106, 112, 255)
TEETH = (255, 252, 246, 255)
TEAR = (132, 200, 255, 225)
EYE_CV = 0.565
EYE_DU = 0.128
MOUTH_CV = 0.355


class Pen:
    def __init__(self):
        self.size = CELL * SS
        self.img = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def p(self, u, v):
        return (u * self.size, (1 - v) * self.size)

    def poly(self, pts, fill=None, outline=None, width=0.0):
        xy = [self.p(u, v) for u, v in pts]
        if fill:
            self.d.polygon(xy, fill=fill)
        if outline and width:
            self.line(pts + [pts[0]], outline, width)

    def line(self, pts, color, width):
        w = max(1, int(width * self.size))
        xy = [self.p(u, v) for u, v in pts]
        self.d.line(xy, fill=color, width=w, joint="curve")
        r = w / 2
        for x, y in (xy[0], xy[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def taper(self, pts, color, w0, w1):
        """Stroke whose width tapers from w0 to w1 along the points."""
        n = len(pts)
        left, right = [], []
        for i, (u, v) in enumerate(pts):
            a = pts[max(0, i - 1)]
            b = pts[min(n - 1, i + 1)]
            du, dv = (b[0] - a[0]) / AX, b[1] - a[1]
            L = math.hypot(du, dv) or 1
            nu, nv = -dv / L * AX, du / L
            w = (w0 + (w1 - w0) * i / max(1, n - 1)) / 2
            left.append((u + nu * w, v + nv * w))
            right.append((u - nu * w, v - nv * w))
        self.poly(left + right[::-1], fill=color)
        for (u, v), w in ((pts[0], w0), (pts[-1], w1)):
            self.ellipse(u, v, w / 2, w / 2, color)

    def ellipse(self, u, v, ru, rv, fill, outline=None, width=0.0):
        x0, y0 = self.p(u - ru * AX, v + rv)
        x1, y1 = self.p(u + ru * AX, v - rv)
        self.d.ellipse([x0, y0, x1, y1], fill=fill)
        if outline and width:
            self.d.ellipse([x0, y0, x1, y1], outline=outline, width=max(1, int(width * self.size)))

    def done(self, blur=0):
        im = self.img
        if blur:
            im = im.filter(ImageFilter.GaussianBlur(blur))
        return im.resize((CELL, CELL), Image.LANCZOS)


def arc(cu, cv, hw, depth, n=24, sign=1):
    """Points of a parabola-like arc; sign=1 curves down in the middle (smile)."""
    return [(cu + hw * (2 * i / (n - 1) - 1), cv - sign * depth * (1 - (2 * i / (n - 1) - 1) ** 2)) for i in range(n)]


# ------------------------------------------------------------------ eyes
def eye_shape(pen, side, cu, cv, hw=0.052, hh=0.072, k_top=1.0, k_bot=1.0, tilt_out=0.0, tilt_in=0.0, wet=False, lash=0.016):
    """side: -1 for the eye on the viewer's left (character's right)."""
    n = 36
    top, bot = [], []
    for i in range(n):
        x = 2 * i / (n - 1) - 1
        outer = x * side  # +1 at the outer corner
        s = math.sqrt(max(0.0, 1 - x * x))
        t = cv + hh * s * k_top + tilt_out * hh * max(0.0, outer) * 1.0 + tilt_in * hh * max(0.0, -outer)
        b = cv - hh * s * k_bot
        if t < b:
            t = b = (t + b) / 2
        top.append((cu + hw * x, t))
        bot.append((cu + hw * x, b))
    shape = top + bot[::-1]
    pen.poly(shape, fill=WHITE)
    if wet:
        pen.poly([(u, v) for u, v in bot[4:-4]] + [(u, v + hh * 0.35) for u, v in bot[4:-4]][::-1], fill=(190, 225, 255, 255))
    pen.line(bot, INK, 0.010)
    pen.line(top, INK, lash)
    # outer lash flick
    end = top[-1] if side > 0 else top[0]
    pen.line([end, (end[0] + side * hw * 0.32, end[1] + hh * 0.18)], INK, lash * 0.8)


def brow(pen, side, cu, cv, inner=0.0, outer=0.0, arch=0.012, w0=0.024, w1=0.012, hw=0.06):
    pts = []
    n = 14
    for i in range(n):
        t = i / (n - 1)  # 0 inner -> 1 outer
        u = cu - side * hw + side * 2 * hw * t
        v = cv + inner + (outer - inner) * t + arch * math.sin(math.pi * t)
        pts.append((u, v))
    pen.taper(pts, INK, w0, w1)


def closed_line(pen, side, cu, cv, curve, hw=0.055, width=0.017):
    pen.line(arc(cu, cv, hw, curve, sign=1), INK, width)


def draw_eyes(name):
    pen = Pen()
    for side in (-1, 1):
        cu = 0.5 + side * EYE_DU
        cv = EYE_CV
        bv = cv + 0.118
        if name == "open":
            eye_shape(pen, side, cu, cv)
            brow(pen, side, cu, bv, 0.0, -0.004)
        elif name == "happy":
            eye_shape(pen, side, cu, cv, k_top=0.95, k_bot=-0.35)
            brow(pen, side, cu, bv + 0.01, 0.0, -0.006)
        elif name == "closed":
            closed_line(pen, side, cu, cv - 0.01, -0.022)
            brow(pen, side, cu, bv - 0.008, 0.0, -0.006)
        elif name == "closed_happy":
            closed_line(pen, side, cu, cv, 0.034)
            brow(pen, side, cu, bv + 0.012, 0.0, -0.004)
        elif name == "closed_sad":
            closed_line(pen, side, cu, cv - 0.012, -0.026)
            brow(pen, side, cu, bv - 0.004, 0.034, -0.014, arch=-0.004)
        elif name == "sad":
            eye_shape(pen, side, cu, cv - 0.004, k_top=0.9, tilt_out=-0.45)
            brow(pen, side, cu, bv - 0.004, 0.032, -0.016, arch=-0.004)
        elif name == "sad_wet":
            eye_shape(pen, side, cu, cv - 0.004, hw=0.056, hh=0.078, k_top=0.92, tilt_out=-0.4, wet=True)
            brow(pen, side, cu, bv - 0.002, 0.036, -0.016, arch=-0.004)
        elif name == "wide":
            eye_shape(pen, side, cu, cv + 0.006, hw=0.06, hh=0.088, k_top=1.08, k_bot=1.0, lash=0.014)
            brow(pen, side, cu, bv + 0.038, 0.006, 0.0, arch=0.02)
        elif name == "worried":
            eye_shape(pen, side, cu, cv, k_top=0.95, tilt_in=0.15)
            brow(pen, side, cu, bv + 0.012, 0.03, -0.006, arch=-0.006)
        elif name == "angry":
            eye_shape(pen, side, cu, cv, k_top=0.85, tilt_in=-0.55)
            brow(pen, side, cu, bv - 0.012, -0.03, 0.012, arch=0.0, w0=0.028)
        elif name == "determined":
            eye_shape(pen, side, cu, cv, k_top=0.66, tilt_in=-0.2)
            brow(pen, side, cu, bv - 0.016, -0.012, 0.0, arch=0.002, w0=0.026)
        elif name == "soft":
            eye_shape(pen, side, cu, cv - 0.004, k_top=0.56, k_bot=0.95)
            brow(pen, side, cu, bv - 0.002, 0.008, -0.01, arch=0.006)
        elif name == "half":
            eye_shape(pen, side, cu, cv - 0.01, k_top=0.32, k_bot=0.9)
            brow(pen, side, cu, bv - 0.008, 0.0, -0.01)
        elif name == "grumpy":
            eye_shape(pen, side, cu, cv - 0.006, k_top=0.48, tilt_in=-0.25)
            brow(pen, side, cu, bv - 0.024, -0.018, -0.002, arch=0.0, w0=0.032, w1=0.02)
        elif name == "shut_tight":
            u0 = cu - side * 0.05
            pen.line([(u0, cv + 0.035), (cu + side * 0.045, cv), (u0, cv - 0.035)], INK, 0.018)
            brow(pen, side, cu, bv - 0.006, 0.03, -0.02, arch=-0.006)
        elif name == "look_up":
            eye_shape(pen, side, cu, cv + 0.004, k_top=1.02)
            brow(pen, side, cu, bv + 0.02, 0.018, 0.002, arch=0.012)
    return pen.done()


def draw_pupils():
    pen = Pen()
    for side in (-1, 1):
        cu = 0.5 + side * EYE_DU
        cv = EYE_CV - 0.006
        pen.ellipse(cu, cv, 0.041, 0.05, (38, 26, 24, 255))
        pen.ellipse(cu, cv, 0.03, 0.037, (20, 14, 14, 255))
        pen.ellipse(cu + 0.016, cv + 0.02, 0.013, 0.014, WHITE)
        pen.ellipse(cu - 0.014, cv - 0.022, 0.006, 0.006, (255, 255, 255, 230))
    return pen.done()


# ------------------------------------------------------------------ mouths
def open_mouth(pen, pts, tongue=True, teeth_top=False, teeth_bot=False):
    pen.poly(pts, fill=MOUTH_IN)
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    cu = (min(us) + max(us)) / 2
    if tongue:
        pen.ellipse(cu, min(vs) + (max(vs) - min(vs)) * 0.22, (max(us) - min(us)) / AX * 0.3, (max(vs) - min(vs)) * 0.28, TONGUE)
    if teeth_top:
        top = max(vs)
        pen.poly([(min(us) + 0.012, top - 0.004), (max(us) - 0.012, top - 0.004), (max(us) - 0.02, top - 0.022), (min(us) + 0.02, top - 0.022)], fill=TEETH)
    if teeth_bot:
        bot = min(vs)
        pen.poly([(min(us) + 0.02, bot + 0.004), (max(us) - 0.02, bot + 0.004), (max(us) - 0.026, bot + 0.018), (min(us) + 0.026, bot + 0.018)], fill=TEETH)
    pen.poly(pts, outline=INK, width=0.011)


def ellipse_pts(cu, cv, ru, rv, n=40):
    return [(cu + ru * math.cos(2 * math.pi * i / n) * AX, cv + rv * math.sin(2 * math.pi * i / n)) for i in range(n)]


def draw_mouth(name):
    pen = Pen()
    cu, cv = 0.5, MOUTH_CV
    W = 0.016
    if name == "flat":
        pen.line([(cu - 0.05, cv), (cu + 0.05, cv)], INK, W)
    elif name == "smile":
        pen.line(arc(cu, cv + 0.012, 0.075, 0.034), INK, W)
    elif name == "grin":
        top = [(cu - 0.085 + 0.17 * i / 20, cv + 0.02 - 0.006 * math.sin(math.pi * i / 20)) for i in range(21)]
        bot = [(cu + 0.085 - 0.17 * i / 20, cv + 0.02 - 0.075 * math.sin(math.pi * i / 20) ** 0.8) for i in range(21)]
        open_mouth(pen, top + bot, teeth_top=True)
    elif name == "frown":
        pen.line(arc(cu, cv - 0.018, 0.065, -0.03), INK, W)
    elif name == "wobble":
        pts = [(cu - 0.07 + 0.14 * i / 30, cv - 0.012 + 0.022 * math.sin(math.pi * i / 30) + 0.007 * math.sin(i * 0.9)) for i in range(31)]
        pen.line(pts, INK, W)
    elif name == "o_small":
        open_mouth(pen, ellipse_pts(cu, cv - 0.004, 0.026, 0.032), tongue=False)
    elif name == "o_big":
        open_mouth(pen, ellipse_pts(cu, cv - 0.018, 0.045, 0.06))
    elif name == "talk_a":
        top = [(cu - 0.06 + 0.12 * i / 20, cv + 0.012 + 0.006 * math.sin(math.pi * i / 20)) for i in range(21)]
        bot = [(cu + 0.06 - 0.12 * i / 20, cv + 0.012 - 0.06 * math.sin(math.pi * i / 20) ** 0.7) for i in range(21)]
        open_mouth(pen, top + bot, teeth_top=True)
    elif name == "talk_e":
        top = [(cu - 0.068 + 0.136 * i / 20, cv + 0.008 + 0.004 * math.sin(math.pi * i / 20)) for i in range(21)]
        bot = [(cu + 0.068 - 0.136 * i / 20, cv + 0.008 - 0.03 * math.sin(math.pi * i / 20) ** 0.7) for i in range(21)]
        open_mouth(pen, top + bot, tongue=False, teeth_top=True)
    elif name == "talk_o":
        open_mouth(pen, ellipse_pts(cu, cv - 0.01, 0.034, 0.045))
    elif name == "smirk":
        pts = [(cu - 0.05 + 0.11 * i / 20, cv - 0.004 + 0.028 * (i / 20) ** 2) for i in range(21)]
        pen.line(pts, INK, W)
    elif name == "grit":
        top = [(cu - 0.07, cv + 0.016), (cu + 0.07, cv + 0.016)]
        bot = [(cu + 0.066, cv - 0.022), (cu - 0.066, cv - 0.022)]
        pen.poly(top + bot, fill=TEETH)
        for k in range(1, 6):
            u = cu - 0.07 + 0.14 * k / 6
            pen.line([(u, cv + 0.014), (u, cv - 0.02)], (150, 140, 140, 255), 0.004)
        pen.line([(cu - 0.068, cv - 0.003), (cu + 0.068, cv - 0.003)], (150, 140, 140, 255), 0.004)
        pen.poly(top + bot, outline=INK, width=0.012)
    elif name == "soft_smile":
        pen.line(arc(cu, cv + 0.008, 0.05, 0.02), INK, W * 0.9)
    elif name == "flat_small":
        pen.line([(cu - 0.03, cv), (cu + 0.03, cv)], INK, W * 0.9)
    elif name == "frown_small":
        pen.line(arc(cu, cv - 0.012, 0.042, -0.018), INK, W * 0.9)
    elif name == "shout":
        top = [(cu - 0.075 + 0.15 * i / 20, cv + 0.01 - 0.012 * math.sin(math.pi * i / 20)) for i in range(21)]
        bot = [(cu + 0.06 - 0.12 * i / 20, cv - 0.07 - 0.012 * math.sin(math.pi * i / 20)) for i in range(21)]
        open_mouth(pen, top + bot, teeth_top=True)
    return pen.done()


# ------------------------------------------------------------------ extras
def tear_stream(pen, cu, cv, side):
    pts = [(cu + side * 0.01 + side * 0.008 * math.sin(i * 0.5), cv - 0.06 - 0.36 * i / 20) for i in range(21)]
    pen.taper(pts, TEAR, 0.026, 0.04)
    pen.line([(u - side * 0.006, v) for u, v in pts[2:16]], (235, 248, 255, 200), 0.006)
    pen.ellipse(cu, cv - 0.06, 0.045, 0.014, TEAR)


def draw_extra(name):
    pen = Pen()
    if name == "tears":
        for side in (-1, 1):
            tear_stream(pen, 0.5 + side * EYE_DU + side * 0.012, EYE_CV, side)
        return pen.done()
    if name == "tear_drop":
        cu, cv = 0.5 - EYE_DU - 0.01, EYE_CV - 0.13
        pen.ellipse(cu, cv, 0.017, 0.022, TEAR)
        pen.poly([(cu - 0.015, cv + 0.008), (cu + 0.015, cv + 0.008), (cu, cv + 0.05)], fill=TEAR)
        pen.ellipse(cu + 0.005, cv + 0.006, 0.005, 0.006, (245, 252, 255, 230))
        for side in (-1, 1):
            u = 0.5 + side * EYE_DU
            pen.line([(u - 0.04, EYE_CV - 0.068), (u + 0.04, EYE_CV - 0.068)], (150, 210, 255, 160), 0.01)
        return pen.done()
    if name == "blush":
        for side in (-1, 1):
            pen.ellipse(0.5 + side * 0.17, 0.44, 0.06, 0.03, (255, 120, 135, 120))
        return pen.done(blur=10)
    return pen.done()


def atlas(cells):
    im = Image.new("RGBA", (CELL * 4, CELL * 4), (0, 0, 0, 0))
    for i, c in enumerate(cells):
        im.paste(c, ((i % 4) * CELL, (i // 4) * CELL))
    return im


def build_atlases(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    eyes = atlas([draw_eyes(n) for n in EYES])
    mouths = atlas([draw_mouth(n) for n in MOUTHS])
    extras = atlas([draw_extra(n) for n in EXTRAS])
    for name, im in (("eyes", eyes), ("mouths", mouths), ("extras", extras), ("pupils", draw_pupils())):
        p = out_dir / f"face_{name}.png"
        im.save(p)
        paths[name] = p
    return paths


def draw_detail(look, path):
    """Per-character static face details: moustache, beard shadow, wrinkles, freckles."""
    pen = Pen()
    any_ = False

    def rgba(hexs, a=255):
        h = hexs.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)
    if look.get("beard"):
        any_ = True
        c = rgba(look["beard"], 110)
        pts = [(0.30, 0.42), (0.36, 0.24), (0.5, 0.17), (0.64, 0.24), (0.70, 0.42), (0.62, 0.30), (0.5, 0.27), (0.38, 0.30)]
        pen.poly(pts, fill=c)
        pen.poly([(0.44, 0.40), (0.56, 0.40), (0.58, 0.385), (0.42, 0.385)], fill=rgba(look["beard"], 150))
    if look.get("mustache"):
        any_ = True
        c = rgba(look["mustache"])
        for side in (-1, 1):
            pts = [(0.5 + side * (0.008 + 0.075 * i / 12), MOUTH_CV + 0.05 - 0.012 * (i / 12) ** 1.6 + 0.008 * math.sin(math.pi * i / 12)) for i in range(13)]
            pen.taper(pts, c, 0.036, 0.016)
        pen.taper([(0.5 + 0.07 * (i / 12 - 0.5) * 2, MOUTH_CV + 0.058) for i in range(13)], rgba(look["mustache"], 255), 0.02, 0.02)
    if look.get("wrinkles"):
        any_ = True
        c = (120, 70, 55, 120)
        for k in range(2):
            pen.line(arc(0.5, 0.80 + 0.03 * k, 0.09 - 0.02 * k, -0.008), c, 0.006)
        for side in (-1, 1):
            u = 0.5 + side * (EYE_DU + 0.07)
            pen.line([(u, EYE_CV + 0.01), (u + side * 0.02, EYE_CV + 0.02)], c, 0.005)
            pen.line([(u, EYE_CV - 0.01), (u + side * 0.02, EYE_CV - 0.02)], c, 0.005)
            pen.line(arc(0.5 + side * 0.1, MOUTH_CV + 0.02, 0.012, 0.03 * side * 0), c, 0.005)
    if look.get("freckles"):
        any_ = True
        for i, (u, v) in enumerate([(0.33, 0.45), (0.36, 0.43), (0.31, 0.42), (0.67, 0.45), (0.64, 0.43), (0.69, 0.42)]):
            pen.ellipse(u, v, 0.006, 0.006, (150, 90, 60, 160))
    im = pen.done()
    im.save(path)
    return any_
