"""Build, bake and render an episode in Blender from its screenplay.

    blender -b -P scripts/kit/blender_build.py -- EPISODE_DIR --preset board|preview|final \
        [--out DIR] [--render] [--frames A-B] [--stills 12,48,...]

Compiles the same plan QC checked (kit.layout), builds R6 characters, sets, lights,
cameras and FX, bakes every frame, saves episode.blend and optionally renders PNG
frames (resumable: existing frames are skipped).
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector, Euler, Matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from kit import layout as L  # noqa: E402
from kit import faces  # noqa: E402
from kit import fonts  # noqa: E402
from kit.rig import Body, PARTS, ORDER, JOINTS, HEAD_RADIUS, HEAD_HEIGHT  # noqa: E402
from kit.acting import EYES, MOUTHS, EXTRAS  # noqa: E402
from kit import camera as camlib  # noqa: E402

PRESETS = {
    "board": dict(engine="BLENDER_WORKBENCH", res=(360, 640), samples=1),
    "preview": dict(engine="BLENDER_EEVEE", res=(540, 960), samples=24),
    "final": dict(engine="BLENDER_EEVEE", res=(1080, 1920), samples=32),
    "final_hq": dict(engine="BLENDER_EEVEE", res=(1080, 1920), samples=64),
}


# ===================================================================== utils
def rgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def new_obj(name, mesh, parent=None, coll=None):
    o = bpy.data.objects.new(name, mesh)
    (coll or bpy.context.scene.collection).objects.link(o)
    if parent:
        o.parent = parent
    return o


def empty(name, parent=None, coll=None, loc=(0, 0, 0)):
    o = bpy.data.objects.new(name, None)
    o.empty_display_size = 0.1
    (coll or bpy.context.scene.collection).objects.link(o)
    o.parent = parent
    o.location = loc
    return o


def box_mesh(name, size, bevel=0.0, segs=3):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= size[0]
        v.co.y *= size[1]
        v.co.z *= size[2]
    if bevel > 0:
        b = min(bevel, min(size) * 0.45)
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=b, segments=segs, profile=0.5, affect="EDGES")
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = bevel > 0
    return me


def lathe_mesh(name, profile, segments=40, cap=True):
    """profile: list of (radius, z) from bottom to top."""
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        ring = [bm.verts.new((r * math.cos(2 * math.pi * i / segments), r * math.sin(2 * math.pi * i / segments), z)) for i in range(segments)]
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            bm.faces.new((a[i], a[j], b[j], b[i]))
    if cap:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return me


def cyl_mesh(name, r, h, segs=24, r2=None, bevel=0.0):
    r2 = r if r2 is None else r2
    prof = [(0.0001, -h / 2)]
    if bevel:
        prof += [(r - bevel, -h / 2), (r, -h / 2 + bevel)]
    else:
        prof += [(r, -h / 2)]
    prof += [(r2, h / 2 - bevel)] + ([(r2 - bevel, h / 2)] if bevel else []) + [(0.0001, h / 2)]
    return lathe_mesh(name, prof, segs, cap=False)


def key_vis(obj, frames_visible, n_frames):
    """Key hide_render/hide_viewport from a sorted list of (frame, visible) changes."""
    for f, vis in frames_visible:
        obj.hide_render = not vis
        obj.hide_viewport = not vis
        obj.keyframe_insert("hide_render", frame=f)
        obj.keyframe_insert("hide_viewport", frame=f)


def set_constant(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return
    for fc in fcurves(ad.action):
        for k in fc.keyframe_points:
            k.interpolation = "CONSTANT"


def fcurves(action):
    if hasattr(action, "fcurves") and len(getattr(action, "fcurves", [])):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out


def bake_channels(obj, data_path, frames, values, interp="LINEAR"):
    """Fast keyframing: values is a list of tuples (one per frame) or scalars."""
    if not obj.animation_data:
        obj.animation_data_create()
    if not obj.animation_data.action:
        obj.animation_data.action = bpy.data.actions.new(obj.name + "Action")
    act = obj.animation_data.action
    n = len(values[0]) if isinstance(values[0], (tuple, list)) else 1
    for i in range(n):
        idx = i if n > 1 else 0
        fc = None
        try:
            fc = act.fcurve_ensure_for_datablock(obj, data_path, index=idx) if hasattr(act, "fcurve_ensure_for_datablock") else None
        except Exception:
            fc = None
        if fc is None:
            # Fallback: insert one key to create the curve, then fill.
            if n > 1:
                getattr(obj, data_path.strip('[]"') if not data_path.startswith('[') else data_path)
            obj.keyframe_insert(data_path, index=idx if n > 1 else -1, frame=frames[0])
            fc = next(c for c in fcurves(act) if c.data_path == data_path and c.array_index == idx)
            fc.keyframe_points.clear()
        fc.keyframe_points.add(len(frames))
        co = []
        for f, v in zip(frames, values):
            co += [f, v[i] if n > 1 else v]
        fc.keyframe_points.foreach_set("co", co)
        fc.keyframe_points.foreach_set("interpolation", [{"CONSTANT": 0, "LINEAR": 1, "BEZIER": 2}[interp]] * len(frames))
        fc.update()


# ===================================================================== materials
_MATS = {}


def principled(name, color, rough=0.5, metal=0.0, spec=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    m.diffuse_color = (*color, 1)  # Workbench boards show this colour
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = spec
    return m


def add_noise_color(m, c0, c1, scale=8.0, detail=3.0, mapping=(1, 1, 1), bump=0.0, kind="noise", coords="Object", vertical=False):
    nt = m.node_tree
    n = nt.nodes
    ln = nt.links.new
    b = n["Principled BSDF"]
    tc = n.new("ShaderNodeTexCoord")
    mp = n.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = mapping
    src = tc.outputs["Object" if vertical else coords]
    if vertical:
        # Walls: run the pattern along the wall (x + y) and up it (z), using world space.
        geo = n.new("ShaderNodeNewGeometry")
        sep = n.new("ShaderNodeSeparateXYZ")
        ln(geo.outputs["Position"], sep.inputs[0])
        add = n.new("ShaderNodeMath")
        add.operation = "ADD"
        ln(sep.outputs["X"], add.inputs[0])
        ln(sep.outputs["Y"], add.inputs[1])
        comb = n.new("ShaderNodeCombineXYZ")
        ln(add.outputs[0], comb.inputs["X"])
        ln(sep.outputs["Z"], comb.inputs["Y"])
        src = comb.outputs[0]
    ln(src, mp.inputs["Vector"])
    if kind == "wave":
        tex = n.new("ShaderNodeTexWave")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Distortion"].default_value = 6.0
        tex.inputs["Detail"].default_value = detail
        out = tex.outputs["Fac"]
    elif kind == "brick":
        tex = n.new("ShaderNodeTexBrick")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Color1"].default_value = (*c1, 1)
        tex.inputs["Color2"].default_value = (*c0, 1)
        tex.inputs["Mortar"].default_value = (*[min(1, x * 1.9 + 0.25) for x in c1], 1)
        tex.inputs["Mortar Size"].default_value = 0.018
        ln(mp.outputs["Vector"], tex.inputs["Vector"])
        ln(tex.outputs["Color"], b.inputs["Base Color"])
        if bump:
            bp = n.new("ShaderNodeBump")
            bp.inputs["Strength"].default_value = bump
            ln(tex.outputs["Fac"], bp.inputs["Height"])
            ln(bp.outputs["Normal"], b.inputs["Normal"])
        return m
    elif kind == "checker":
        tex = n.new("ShaderNodeTexChecker")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Color1"].default_value = (*c0, 1)
        tex.inputs["Color2"].default_value = (*c1, 1)
        ln(mp.outputs["Vector"], tex.inputs["Vector"])
        ln(tex.outputs["Color"], b.inputs["Base Color"])
        return m
    else:
        tex = n.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Detail"].default_value = detail
        out = tex.outputs["Fac"]
    ln(mp.outputs["Vector"], tex.inputs["Vector"])
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*c0, 1)
    ramp.color_ramp.elements[1].color = (*c1, 1)
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[1].position = 0.7
    ln(out, ramp.inputs["Fac"])
    ln(ramp.outputs["Color"], b.inputs["Base Color"])
    if bump:
        bp = n.new("ShaderNodeBump")
        bp.inputs["Strength"].default_value = bump
        bp.inputs["Distance"].default_value = 0.02
        ln(out, bp.inputs["Height"])
        ln(bp.outputs["Normal"], b.inputs["Normal"])
    return m


def shade(c, k):
    return tuple(min(1.0, x * k) for x in c)


def emissive(name, color, strength, alpha=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    m.diffuse_color = (*color, 1)
    if alpha is None:
        nt.links.new(em.outputs[0], out.inputs[0])
    else:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.inputs[0].default_value = alpha
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs[0])
        m.surface_render_method = "BLENDED"
    return m


def material(spec, textures=None):
    """Material from a 'kind:#hex[:extra]' spec. Cached."""
    if spec in _MATS:
        return _MATS[spec]
    parts = spec.split(":")
    kind = parts[0]
    col = rgb(parts[1]) if len(parts) > 1 and parts[1].startswith("#") else (0.5, 0.5, 0.5)
    extra = parts[2] if len(parts) > 2 else None
    name = spec
    if kind in ("plastic", "paint", "ceramic"):
        m = principled(name, col, 0.42 if kind == "plastic" else 0.55 if kind == "paint" else 0.3)
        add_noise_color(m, shade(col, 0.96), shade(col, 1.03), scale=3.0)
    elif kind == "carpaint":
        m = principled(name, col, 0.25, spec=0.8)
    elif kind == "wall":
        m = principled(name, col, 0.85)
        add_noise_color(m, shade(col, 0.93), shade(col, 1.04), scale=1.5, detail=4, bump=0.04)
    elif kind == "wallpaper":
        m = principled(name, col, 0.8)
        add_noise_color(m, shade(col, 0.92), shade(col, 1.05), scale=14, mapping=(1, 0.02, 1), kind="wave", vertical=True)
    elif kind in ("wood", "floor_wood"):
        m = principled(name, col, 0.45 if kind == "floor_wood" else 0.55)
        if kind == "floor_wood":
            add_noise_color(m, shade(col, 0.72), shade(col, 1.12), scale=2.0, mapping=(1, 7, 1), kind="brick", bump=0.15)
        else:
            add_noise_color(m, shade(col, 0.78), shade(col, 1.12), scale=3.0, detail=6, mapping=(1, 0.15, 0.15), kind="wave")
    elif kind == "brick":
        m = principled(name, col, 0.8)
        add_noise_color(m, shade(col, 0.82), col, scale=1.6, mapping=(1, 2.2, 1), kind="brick", bump=0.25, vertical=True)
    elif kind in ("concrete", "pavers", "asphalt", "floor_vinyl", "ceiling_tile"):
        rough = {"concrete": 0.85, "pavers": 0.8, "asphalt": 0.9, "floor_vinyl": 0.35, "ceiling_tile": 0.9}[kind]
        m = principled(name, col, rough)
        if kind == "pavers":
            add_noise_color(m, shade(col, 0.86), col, scale=1.25, mapping=(1, 1, 1), kind="brick", bump=0.12)
        elif kind == "floor_vinyl":
            add_noise_color(m, shade(col, 0.93), shade(col, 1.04), scale=0.9, kind="checker")
        elif kind == "ceiling_tile":
            add_noise_color(m, shade(col, 0.9), col, scale=1.6, kind="brick", bump=0.05)
        else:
            add_noise_color(m, shade(col, 0.85), shade(col, 1.08), scale=6, detail=6, bump=0.08)
    elif kind == "grass":
        m = principled(name, col, 0.9)
        add_noise_color(m, shade(col, 0.75), shade(col, 1.15), scale=5, detail=8, bump=0.3)
    elif kind in ("leaf",):
        m = principled(name, col, 0.7)
        add_noise_color(m, shade(col, 0.78), shade(col, 1.12), scale=6, detail=4, bump=0.25)
    elif kind == "bark":
        m = principled(name, col, 0.85)
        add_noise_color(m, shade(col, 0.7), shade(col, 1.1), scale=4, mapping=(1, 1, 0.2), kind="wave")
    elif kind in ("fabric", "rug", "lampshade"):
        m = principled(name, col, 0.9)
        add_noise_color(m, shade(col, 0.9), shade(col, 1.06), scale=60, detail=2, bump=0.05)
        if kind == "lampshade":
            b = m.node_tree.nodes["Principled BSDF"]
            b.inputs["Emission Color"].default_value = (*col, 1)
            b.inputs["Emission Strength"].default_value = 1.6
    elif kind == "metal":
        m = principled(name, col, 0.35, metal=0.8)
    elif kind == "rubber":
        m = principled(name, col, 0.8)
    elif kind == "roof":
        m = principled(name, col, 0.8)
        add_noise_color(m, shade(col, 0.8), col, scale=2.2, mapping=(1, 3, 1), kind="brick", vertical=True)
    elif kind == "glass":
        m = principled(name, col, 0.08, spec=1.0)
    elif kind in ("emit", "screen"):
        strength = float(extra) if extra else 4.0
        m = emissive(name, col, strength)
    elif kind.startswith("window_"):
        m = window_material(name, kind, col)
    elif kind == "photo":
        m = photo_material(name, parts[1], textures)
    elif kind == "monitor":
        m = emissive(name, rgb("#0B2A1E"), 1.0)
        nt = m.node_tree
        wave = nt.nodes.new("ShaderNodeTexWave")
        wave.inputs["Scale"].default_value = 3.0
        wave.inputs["Distortion"].default_value = 0.0
        wave.bands_direction = "X"
        tc = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(tc.outputs["Generated"], wave.inputs["Vector"])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.96
        ramp.color_ramp.elements[0].color = (0.01, 0.05, 0.03, 1)
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = (0.3, 1.0, 0.6, 1)
        nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
        em = [n for n in nt.nodes if n.type == "EMISSION"][0]
        nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
        em.inputs["Strength"].default_value = 3.0
    else:
        m = principled(name, col, 0.5)
    _MATS[spec] = m
    return m


def window_material(name, kind, col):
    """Bright sky/outside backdrop: gradient + soft shapes, emissive so it reads as daylight or night."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    if kind == "window_night":
        ramp.color_ramp.elements[0].color = (*rgb("#0A1022"), 1)
        ramp.color_ramp.elements[1].color = (*rgb("#1F3566"), 1)
        strength = 1.2
    elif kind == "window_class":
        ramp.color_ramp.elements[0].color = (*shade(col, 0.55), 1)
        ramp.color_ramp.elements[1].color = (*col, 1)
        strength = 0.9
    else:
        ramp.color_ramp.elements[0].color = (*col, 1)
        ramp.color_ramp.elements[1].color = (*rgb("#FFF6E8"), 1)
        strength = 2.2
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    col_out = ramp.outputs["Color"]
    if kind == "window_night":
        # City lights: a few warm dots low in the frame.
        vor = nt.nodes.new("ShaderNodeTexVoronoi")
        vor.inputs["Scale"].default_value = 18
        nt.links.new(tc.outputs["Generated"], vor.inputs["Vector"])
        r2 = nt.nodes.new("ShaderNodeValToRGB")
        r2.color_ramp.elements[0].position = 0.0
        r2.color_ramp.elements[0].color = (1.0, 0.75, 0.4, 1)
        r2.color_ramp.elements[1].position = 0.08
        r2.color_ramp.elements[1].color = (0, 0, 0, 1)
        nt.links.new(vor.outputs["Distance"], r2.inputs["Fac"])
        low = nt.nodes.new("ShaderNodeMath")
        low.operation = "LESS_THAN"
        low.inputs[1].default_value = 0.45
        nt.links.new(sep.outputs["Z"], low.inputs[0])
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "SCALE"
        nt.links.new(r2.outputs["Color"], mul.inputs[0])
        nt.links.new(low.outputs[0], mul.inputs["Scale"])
        add = nt.nodes.new("ShaderNodeVectorMath")
        add.operation = "ADD"
        nt.links.new(ramp.outputs["Color"], add.inputs[0])
        nt.links.new(mul.outputs[0], add.inputs[1])
        col_out = add.outputs[0]
    elif kind == "window_class":
        # Classroom interior hints: horizontal bands.
        wave = nt.nodes.new("ShaderNodeTexWave")
        wave.inputs["Scale"].default_value = 2.0
        wave.bands_direction = "Z"
        nt.links.new(tc.outputs["Generated"], wave.inputs["Vector"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs["Factor"].default_value = 0.15
        nt.links.new(ramp.outputs["Color"], mix.inputs["A"])
        nt.links.new(wave.outputs["Color"], mix.inputs["B"])
        col_out = mix.outputs["Result"]
    nt.links.new(col_out, em.inputs["Color"])
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs[0], out.inputs[0])
    return m


def photo_material(name, which, textures):
    path = (textures or {}).get("photo_" + which)
    m = principled(name, (0.8, 0.7, 0.6), 0.35)
    if path:
        img = bpy.data.images.load(str(path))
        nt = m.node_tree
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tc = nt.nodes.new("ShaderNodeTexCoord")
        nt.links.new(tc.outputs["Generated"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return m


def draw_photos(out_dir):
    """Small framed pictures for set dressing (grandma portrait, landscape)."""
    from PIL import Image, ImageDraw
    out = {}
    im = Image.new("RGB", (256, 320), (236, 214, 178))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 256, 320], fill=(196, 170, 140))
    d.rectangle([60, 150, 196, 320], fill=(150, 72, 90))
    d.rounded_rectangle([82, 46, 174, 150], 30, fill=(232, 186, 150))
    d.pieslice([68, 20, 188, 110], 180, 360, fill=(220, 220, 225))
    d.ellipse([70, 40, 96, 120], fill=(220, 220, 225))
    d.ellipse([160, 40, 186, 120], fill=(220, 220, 225))
    for x in (108, 148):
        d.ellipse([x - 8, 86, x + 8, 102], fill=(40, 30, 30))
    d.arc([108, 104, 148, 130], 20, 160, fill=(60, 30, 30), width=4)
    p = out_dir / "photo_grandma.png"
    im.save(p)
    out["photo_grandma"] = p
    im = Image.new("RGB", (320, 240), (150, 200, 235))
    d = ImageDraw.Draw(im)
    d.polygon([(0, 170), (90, 90), (170, 160), (240, 100), (320, 150), (320, 240), (0, 240)], fill=(90, 130, 80))
    d.ellipse([240, 20, 290, 70], fill=(255, 230, 160))
    p = out_dir / "photo_landscape.png"
    im.save(p)
    out["photo_landscape"] = p
    im = Image.new("RGB", (240, 320), (205, 190, 170))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([70, 60, 170, 170], 28, fill=(232, 186, 150))
    d.rectangle([50, 180, 190, 320], fill=(70, 90, 120))
    p = out_dir / "photo_portrait.png"
    im.save(p)
    out["photo_portrait"] = p
    return out


# ===================================================================== sets
def build_set(set_, coll, textures):
    ox, oy, oz = set_.offset
    objs = {}
    for e in set_.elements:
        shape = e["shape"]
        x, y, z = e["at"]
        loc = (x + ox, y + oy, z + oz)
        mat = material(e["mat"], textures)
        e = dict(e, name=f"{set_.id}/{e['name']}")
        if shape == "box":
            me = box_mesh(e["name"], e["size"], e.get("bevel", 0.0 if min(e["size"]) < 0.03 else 0.008), segs=2)
            o = new_obj(e["name"], me, coll=coll)
        elif shape == "cyl":
            r, _, h = e["size"]
            o = new_obj(e["name"], cyl_mesh(e["name"], r, h, 20, bevel=min(0.02, r * 0.2)), coll=coll)
        elif shape == "cone":
            r, r2, h = e["size"]
            o = new_obj(e["name"], cyl_mesh(e["name"], r, h, 20, r2=r2), coll=coll)
        elif shape == "sphere":
            r = e["size"]
            prof = [(r * math.sin(math.pi * i / 10) + 0.0001, -r * math.cos(math.pi * i / 10)) for i in range(11)]
            o = new_obj(e["name"], lathe_mesh(e["name"], prof, 16, cap=False), coll=coll)
        elif shape == "plane":
            w, d, h = e["size"]
            me = box_mesh(e["name"], (max(w, 0.002), max(d, 0.002), max(h, 0.002)))
            o = new_obj(e["name"], me, coll=coll)
        elif shape == "text":
            cu = bpy.data.curves.new(e["name"], "FONT")
            cu.body = e["text"]
            cu.align_x = "CENTER"
            cu.align_y = "CENTER"
            cu.size = e["size"]
            cu.extrude = 0.01
            cu.font = FONT_BOLD
            o = new_obj(e["name"], cu, coll=coll)
            face = e.get("face", "-y")
            o.rotation_euler = {"-y": (math.pi / 2, 0, 0), "y": (math.pi / 2, 0, math.pi), "x": (math.pi / 2, 0, math.pi / 2), "-x": (math.pi / 2, 0, -math.pi / 2)}[face]
        else:
            continue
        o.location = loc
        if shape != "text":
            o.rotation_euler = (math.radians(e.get("rx", 0)), math.radians(e.get("ry", 0)), math.radians(e.get("rot", 0)))
            if shape in ("cyl", "cone") and e.get("rx"):
                pass
        o.data.materials.append(mat)
        objs[e["name"]] = o
    for i, lt in enumerate(set_.lights):
        kind = lt["type"].upper()
        ld = bpy.data.lights.new(f"{set_.id}.light{i}", kind if kind != "AREA" else "AREA")
        ld.color = rgb(lt.get("color", "#FFFFFF"))
        ld.energy = lt.get("power", 50)
        if kind == "AREA":
            ld.size = lt.get("size", 1.0)
            ld.shape = "SQUARE"
        if kind == "POINT":
            ld.shadow_soft_size = lt.get("radius", 0.1)
        if hasattr(ld, "use_custom_distance"):
            ld.use_custom_distance = True
            ld.cutoff_distance = 14
        lo = new_obj(f"{set_.id}.light{i}", ld, coll=coll)
        x, y, z = lt["at"]
        lo.location = (x + ox, y + oy, z + oz)
        rot = lt.get("rot", (0, 0, 0))
        lo.rotation_euler = tuple(math.radians(v) for v in rot)
    return objs


# ===================================================================== characters
def face_material(cid, skin, atlases, detail_path):
    m = bpy.data.materials.new(f"{cid}.face")
    m.diffuse_color = (*skin, 1)
    m.use_nodes = True
    nt = m.node_tree
    n = nt.nodes
    ln = nt.links.new
    b = n["Principled BSDF"]
    b.inputs["Roughness"].default_value = 0.45
    tc = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    ln(tc.outputs["Generated"], sep.inputs[0])

    def math_node(op, a=None, bval=None, inputs=()):
        nd = n.new("ShaderNodeMath")
        nd.operation = op
        if a is not None:
            if isinstance(a, (int, float)):
                nd.inputs[0].default_value = a
            else:
                ln(a, nd.inputs[0])
        if bval is not None:
            if isinstance(bval, (int, float)):
                nd.inputs[1].default_value = bval
            else:
                ln(bval, nd.inputs[1])
        return nd.outputs[0]

    gx = math_node("SUBTRACT", sep.outputs["X"], 0.5)
    gy = math_node("SUBTRACT", 0.5, sep.outputs["Y"])
    ang = math_node("ARCTAN2", gx, gy)
    u = math_node("ADD", math_node("DIVIDE", ang, 2.1), 0.5)
    v = sep.outputs["Z"]

    def attr(name):
        a = n.new("ShaderNodeAttribute")
        a.attribute_type = "OBJECT"
        a.attribute_name = name
        return a.outputs["Fac"]

    def lookup(img_path, index_socket, du=None, dv=None, grid=4):
        uu = u if du is None else math_node("SUBTRACT", u, du)
        vv = v if dv is None else math_node("SUBTRACT", v, dv)
        img = bpy.data.images.load(str(img_path), check_existing=True)
        tex = n.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = "CLIP"
        tex.interpolation = "Cubic"
        if index_socket is None:
            au, av = uu, vv
        else:
            col = math_node("FLOORED_MODULO", index_socket, grid)
            row = math_node("FLOOR", math_node("DIVIDE", index_socket, grid))
            au = math_node("DIVIDE", math_node("ADD", uu, col), grid)
            av = math_node("DIVIDE", math_node("ADD", vv, math_node("SUBTRACT", grid - 1, row)), grid)
        comb = n.new("ShaderNodeCombineXYZ")
        ln(au, comb.inputs["X"])
        ln(av, comb.inputs["Y"])
        ln(comb.outputs[0], tex.inputs["Vector"])
        inside = math_node("MULTIPLY",
                           math_node("MULTIPLY", math_node("GREATER_THAN", uu, 0.003), math_node("LESS_THAN", uu, 0.997)),
                           math_node("MULTIPLY", math_node("GREATER_THAN", vv, 0.003), math_node("LESS_THAN", vv, 0.997)))
        alpha = math_node("MULTIPLY", tex.outputs["Alpha"], inside)
        return tex.outputs["Color"], alpha

    def mix(base, top, fac):
        mx = n.new("ShaderNodeMix")
        mx.data_type = "RGBA"
        ln(fac, mx.inputs["Factor"])
        if isinstance(base, tuple):
            mx.inputs["A"].default_value = (*base, 1)
        else:
            ln(base, mx.inputs["A"])
        ln(top, mx.inputs["B"])
        return mx.outputs["Result"]

    col = skin
    ec, ea = lookup(atlases["extras"], attr("extra"))
    col = mix(col, ec, ea)
    yc, ya = lookup(atlases["eyes"], attr("eyes"))
    col = mix(col, yc, ya)
    gxs = math_node("MULTIPLY", attr("gaze_x"), 0.032)
    gys = math_node("MULTIPLY", attr("gaze_y"), 0.03)
    pc, pa = lookup(atlases["pupils"], None, du=gxs, dv=gys)
    sepc = n.new("ShaderNodeSeparateColor")
    ln(yc, sepc.inputs[0])
    white = math_node("MULTIPLY", math_node("GREATER_THAN", sepc.outputs[0], 0.6), ya)
    col = mix(col, pc, math_node("MULTIPLY", pa, white))
    mc, ma = lookup(atlases["mouths"], attr("mouth"))
    col = mix(col, mc, ma)
    if detail_path:
        dc, da = lookup(detail_path, None)
        col = mix(col, dc, da)
    ln(col, b.inputs["Base Color"])
    return m


def fabric(name, color, rough=0.75, scale=90):
    key = f"cloth:{name}:{color}"
    if key in _MATS:
        return _MATS[key]
    c = rgb(color)
    m = principled(key, c, rough)
    add_noise_color(m, shade(c, 0.92), shade(c, 1.05), scale=scale, detail=2, bump=0.08)
    _MATS[key] = m
    return m


def skin_mat(color):
    key = f"skin:{color}"
    if key not in _MATS:
        _MATS[key] = principled(key, rgb(color), 0.42, spec=0.4)
    return _MATS[key]


def part_obj(name, size, mat, parent, loc, bevel_frac=0.09, coll=None):
    me = box_mesh(name, size, bevel=min(size) * bevel_frac, segs=3)
    o = new_obj(name, me, parent, coll)
    o.location = loc
    me.materials.append(mat)
    return o


def build_hair(cid, style, color, head, s, coll):
    """Hair as chunky blocky pieces parented to the head (head origin = head centre)."""
    hm = fabric("hair", color, 0.55, scale=40)
    R = HEAD_RADIUS * s
    H = HEAD_HEIGHT * s
    top = H / 2
    pieces = []

    def blk(name, loc, size, rot=(0, 0, 0), bev=0.25):
        o = part_obj(f"{cid}.hair.{name}", size, hm, head, loc, bevel_frac=bev, coll=coll)
        o.rotation_euler = rot
        pieces.append(o)
        return o

    def spike(name, base, direction, length, width, twist=0.0):
        bm = bmesh.new()
        w = width / 2
        d = Vector(direction).normalized()
        up = Vector((0, 0, 1)) if abs(d.z) < 0.9 else Vector((1, 0, 0))
        side = d.cross(up).normalized()
        nrm = side.cross(d).normalized()
        b0 = Vector(base)
        vs = [bm.verts.new(b0 + side * w * sx + nrm * w * 0.55 * sy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        tip = bm.verts.new(b0 + d * length + nrm * twist)
        for i in range(4):
            bm.faces.new((vs[i], vs[(i + 1) % 4], tip))
        bm.faces.new(list(reversed(vs)))
        me = bpy.data.meshes.new(f"{cid}.hair.{name}")
        bm.to_mesh(me)
        bm.free()
        me.materials.append(hm)
        o = new_obj(f"{cid}.hair.{name}", me, head, coll)
        pieces.append(o)
        return o

    def cap(z0=0.12, back_low=None, r_add=0.06):
        prof = [(R + r_add * s / 0.33 * 0.33, z0 * H)]
        for i in range(1, 9):
            a = i / 8 * math.pi / 2
            prof.append(((R + r_add * s) * math.cos(a) + 0.0001, top + 0.02 * s + (H * 0.5 - z0 * H + 0.02 * s) * 0 + (top - z0 * H) * 0 + 0.16 * s * math.sin(a) - 0.0 + (z0 * H - z0 * H)))
        # Simple dome: ring at z0, then dome over the top
        prof = [(R + r_add * s, z0 * H)] + [((R + r_add * s) * math.cos(a) + 0.0001, top - 0.02 * s + 0.22 * s * math.sin(a)) for a in [i / 8 * math.pi / 2 for i in range(0, 9)]]
        me = lathe_mesh(f"{cid}.hair.cap", prof, 40, cap=True)
        me.materials.append(hm)
        o = new_obj(f"{cid}.hair.cap", me, head, coll)
        pieces.append(o)
        if back_low is not None:
            # Back of head covered down to back_low (fraction of height from bottom).
            bm = bmesh.new()
            segs = 22
            a0, a1 = math.radians(60), math.radians(300)
            rings = []
            for z in (back_low * H - H / 2, z0 * H + 0.02):
                ring = []
                for i in range(segs + 1):
                    a = a0 + (a1 - a0) * i / segs
                    rr = R + r_add * s
                    ring.append(bm.verts.new((rr * math.sin(a), -rr * math.cos(a), z)))
                rings.append(ring)
            for i in range(segs):
                bm.faces.new((rings[0][i], rings[0][i + 1], rings[1][i + 1], rings[1][i]))
            me2 = bpy.data.meshes.new(f"{cid}.hair.back")
            bm.to_mesh(me2)
            bm.free()
            for p in me2.polygons:
                p.use_smooth = True
            me2.materials.append(hm)
            sol = new_obj(f"{cid}.hair.back", me2, head, coll)
            mod = sol.modifiers.new("thick", "SOLIDIFY")
            mod.thickness = 0.05 * s / 0.33
            pieces.append(sol)
        return o

    def locks(prefix, n, crown=True, length=0.62, width=0.55, fringe=5, fringe_len=0.36, seed=1):
        import random
        rnd = random.Random(seed)
        if crown:
            for i in range(n):
                a = math.radians(-160 + 320 * i / (n - 1)) + rnd.uniform(-0.08, 0.08)
                if abs(math.degrees(a)) < 40:
                    continue
                el = math.radians(rnd.uniform(10, 45))
                base = ((R * 0.82) * math.sin(a), (R * 0.82) * math.cos(a), top * 0.62)
                d = (math.sin(a) * math.cos(el), math.cos(a) * math.cos(el), math.sin(el) + 0.15)
                spike(f"{prefix}s{i}", base, d, length * s * rnd.uniform(0.85, 1.15), width * s)
            for i in range(5):
                ang = i * 1.256 + 0.3
                base = (R * 0.55 * math.cos(ang), R * 0.45 * math.sin(ang) + 0.06 * s, top + 0.12 * s)
                spike(f"{prefix}t{i}", base, (0.25 * math.cos(ang), 0.45, 0.85), length * 0.8 * s, width * s)
        # Fringe: short chunky locks that stop above the brows and sweep to one side.
        for i in range(fringe):
            x = (-0.5 + i / max(1, fringe - 1)) * R * 1.35
            base = (x, -R * 0.74, top * 0.98)
            d = (0.55, -0.55, -0.62)
            spike(f"{prefix}f{i}", base, d, fringe_len * s * (1.05 - 0.1 * abs(i - fringe // 2)), 0.5 * s, twist=-0.02 * s)

    if style == "spiky":
        cap(0.5, back_low=0.12)
        locks("k", 16, seed=hash(cid) & 0xFF)
    elif style == "short":
        cap(0.52, back_low=0.16, r_add=0.04)
        locks("d", 14, length=0.32, width=0.6, fringe=4, fringe_len=0.28, seed=5)
    elif style == "bob":
        cap(0.30, back_low=0.0, r_add=0.09)
        blk("fringe", (0, -R * 0.72, top * 0.86), (R * 1.75, R * 0.5, 0.32 * s), rot=(0.18, 0, 0), bev=0.35)
        for sd in (-1, 1):
            blk(f"side{sd}", (sd * R * 0.92, -R * 0.2, -0.05 * s), (0.26 * s, R * 1.3, H * 0.85), bev=0.35)
    elif style == "long":
        import random
        rnd = random.Random(7)
        cap(0.42, back_low=0.0, r_add=0.05)
        # Flowing locks down the back and sides: long tapered blades from the scalp.
        for i in range(13):
            a = math.radians(70 + 220 * i / 12) + rnd.uniform(-0.05, 0.05)
            base = ((R + 0.02 * s) * math.sin(a), -(R + 0.02 * s) * math.cos(a), top * 0.55)
            out = (math.sin(a) * 0.22, -math.cos(a) * 0.22, -1.0)
            spike(f"lock{i}", base, out, H * rnd.uniform(1.15, 1.35), 0.62 * s)
        # Swept fringe and face-framing locks that stop above the brows.
        for i in range(4):
            x = (-0.45 + 0.3 * i) * R
            spike(f"fr{i}", (x, -R * 0.76, top * 0.98), (0.75, -0.45, -0.5), 0.42 * s * (1.1 - 0.12 * i), 0.55 * s)
        for sd in (-1, 1):
            spike(f"frame{sd}", (sd * R * 0.86, -R * 0.45, top * 0.7), (sd * 0.15, -0.1, -1.0), H * 0.95, 0.5 * s)
    elif style == "pigtails":
        cap(0.36, back_low=0.2, r_add=0.06)
        blk("fringe", (0, -R * 0.72, top * 0.86), (R * 1.7, R * 0.5, 0.3 * s), rot=(0.2, 0, 0), bev=0.35)
        for sd in (-1, 1):
            blk(f"tail{sd}", (sd * (R + 0.35 * s), 0.1 * s, -0.1 * s), (0.55 * s, 0.55 * s, 0.95 * s), rot=(0, sd * 0.35, 0), bev=0.45)
            band = part_obj(f"{cid}.band{sd}", (0.3 * s, 0.3 * s, 0.14 * s), fabric("band", "#F2547D"), head, (sd * (R + 0.12 * s), 0.1 * s, 0.25 * s), 0.3, coll)
            pieces.append(band)
    elif style == "bun":
        cap(0.36, back_low=0.15, r_add=0.06)
        prof = [(0.0001, -0.32 * s)] + [(0.32 * s * math.sin(math.pi * i / 10) + 0.0001, -0.32 * s * math.cos(math.pi * i / 10)) for i in range(1, 11)]
        me = lathe_mesh(f"{cid}.hair.bun", prof, 20, cap=False)
        me.materials.append(hm)
        o = new_obj(f"{cid}.hair.bun", me, head, coll)
        o.location = (0, R * 0.55, top + 0.12 * s)
        pieces.append(o)
        blk("fringe", (0.1 * s, -R * 0.7, top * 0.86), (R * 1.6, R * 0.45, 0.26 * s), rot=(0.25, 0, -0.15), bev=0.35)
    elif style == "bald_sides":
        for sd in (-1, 1):
            blk(f"tuft{sd}", (sd * (R * 0.93), R * 0.15, 0.02 * s), (0.24 * s, R * 1.2, 0.42 * s), bev=0.4)
        blk("backband", (0, R * 0.88, 0.0), (R * 1.5, 0.24 * s, 0.42 * s), bev=0.4)
    return pieces


def build_glasses(cid, color, head, s, coll):
    m = principled(f"{cid}.glasses", rgb(color), 0.3, metal=0.6)
    R = HEAD_RADIUS * s
    zc = (faces.EYE_CV - 0.5) * HEAD_HEIGHT * s
    for sd in (-1, 1):
        ang = sd * faces.EYE_DU * 2.1
        x = (R + 0.035 * s) * math.sin(ang)
        y = -(R + 0.035 * s) * math.cos(ang)
        bm = bmesh.new()
        bmesh.ops.create_circle(bm, cap_ends=False, radius=0.19 * s, segments=24)
        me = bpy.data.meshes.new(f"{cid}.lens{sd}")
        bm.to_mesh(me)
        bm.free()
        o = new_obj(f"{cid}.lens{sd}", me, head, coll)
        o.location = (x, y - 0.02 * s, zc)
        o.rotation_euler = (math.pi / 2, 0, ang)
        mod = o.modifiers.new("w", "SKIN") if False else None
        sk = o.modifiers.new("ring", "SCREW") if False else None
        o.data.materials.append(m)
        # Give the rim thickness with a skin-free trick: convert to curve bevel.
        cu = bpy.data.curves.new(f"{cid}.rim{sd}", "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("POLY")
        sp.points.add(24)
        for i in range(25):
            a = 2 * math.pi * i / 24
            sp.points[i].co = (0.115 * s * math.cos(a), 0.10 * s * math.sin(a), 0, 1)
        cu.bevel_depth = 0.014 * s
        co = new_obj(f"{cid}.rim{sd}", cu, head, coll)
        co.location = o.location
        co.rotation_euler = o.rotation_euler
        cu.materials.append(m)
        bpy.data.objects.remove(o)
    bridge = part_obj(f"{cid}.bridge", (0.1 * s, 0.03 * s, 0.03 * s), m, head, (0, -(R + 0.03 * s), zc + 0.02 * s), 0.3, coll)


def build_outfit(cid, body, look, outfit, joints, coll, tag):
    """Torso, arms (sleeve + hand) and legs (pants + shoe) for one outfit."""
    s = body.s
    skin = skin_mat(look["skin"])
    top = outfit["top"]
    style = top["style"]
    tm = fabric(style, top["color"])
    trim = fabric("trim", top.get("trim", "#FFFFFF"))
    pants = fabric("pants", outfit.get("pants", "#333333"), scale=110)
    shoes = fabric("shoes", outfit.get("shoes", "#222222"), 0.5, scale=30)
    objs = []
    _, tc, tsz = body.part("torso")
    t = part_obj(f"{cid}.{tag}.torso", tsz, tm, joints["pelvis"], tc, 0.07, coll)
    objs.append(t)
    W, D, Hh = tsz
    front = -D / 2 - 0.004
    if style == "hoodie":
        objs.append(part_obj(f"{cid}.{tag}.pocket", (W * 0.62, 0.03 * s, Hh * 0.26), fabric("hoodie_p", top["color"], 0.8), joints["pelvis"], (0, front - 0.01 * s, Hh * 0.26), 0.3, coll))
        for sd in (-1, 1):
            objs.append(part_obj(f"{cid}.{tag}.string{sd}", (0.06 * s, 0.03 * s, Hh * 0.28), trim, joints["pelvis"], (sd * 0.18 * s, front - 0.01 * s, Hh * 0.78), 0.3, coll))
        objs.append(part_obj(f"{cid}.{tag}.hood", (W * 0.8, D * 0.45, Hh * 0.22), tm, joints["pelvis"], (0, D * 0.42, Hh * 0.98), 0.35, coll))
        objs.append(part_obj(f"{cid}.{tag}.hem", (W * 1.005, D * 1.005, Hh * 0.08), trim if top.get("trim_hem") else fabric("rib", top["color"], 0.85, 200), joints["pelvis"], (0, 0, Hh * 0.04), 0.2, coll))
    elif style == "cardigan":
        objs.append(part_obj(f"{cid}.{tag}.inner", (W * 0.3, 0.02 * s, Hh * 0.96), trim, joints["pelvis"], (0, front, Hh * 0.5), 0.2, coll))
        for k in range(4):
            objs.append(part_obj(f"{cid}.{tag}.btn{k}", (0.07 * s, 0.03 * s, 0.07 * s), fabric("btn", "#3A2A20", 0.3), joints["pelvis"], (W * 0.17, front - 0.012 * s, Hh * (0.2 + 0.19 * k)), 0.4, coll))
        for sd in (-1, 1):
            objs.append(part_obj(f"{cid}.{tag}.pocket{sd}", (W * 0.24, 0.025 * s, Hh * 0.16), tm, joints["pelvis"], (sd * W * 0.3, front - 0.008 * s, Hh * 0.22), 0.3, coll))
    elif style == "shirt":
        objs.append(part_obj(f"{cid}.{tag}.placket", (0.1 * s, 0.02 * s, Hh * 0.95), trim, joints["pelvis"], (0, front, Hh * 0.5), 0.2, coll))
        for sd in (-1, 1):
            c = part_obj(f"{cid}.{tag}.collar{sd}", (W * 0.2, 0.04 * s, Hh * 0.12), tm, joints["pelvis"], (sd * W * 0.12, front - 0.01 * s, Hh * 0.93), 0.3, coll)
            c.rotation_euler = (0, sd * 0.5, 0)
            objs.append(c)
        objs.append(part_obj(f"{cid}.{tag}.chestpocket", (W * 0.2, 0.02 * s, Hh * 0.14), trim, joints["pelvis"], (-W * 0.25, front - 0.006 * s, Hh * 0.7), 0.2, coll))
        objs.append(part_obj(f"{cid}.{tag}.belt", (W * 1.01, D * 1.01, Hh * 0.07), fabric("belt", "#2A1E18", 0.4), joints["pelvis"], (0, 0, Hh * 0.035), 0.2, coll))
    elif style == "sweater":
        objs.append(part_obj(f"{cid}.{tag}.hem", (W * 1.005, D * 1.005, Hh * 0.1), fabric("rib", top["color"], 0.9, 200), joints["pelvis"], (0, 0, Hh * 0.05), 0.2, coll))
        objs.append(part_obj(f"{cid}.{tag}.collar", (W * 0.42, D * 0.6, Hh * 0.06), trim, joints["pelvis"], (0, 0, Hh * 0.99), 0.3, coll))
        objs.append(part_obj(f"{cid}.{tag}.stripe", (W * 1.004, D * 1.004, Hh * 0.08), trim, joints["pelvis"], (0, 0, Hh * 0.62), 0.1, coll))
    elif style == "tee":
        objs.append(part_obj(f"{cid}.{tag}.print", (W * 0.36, 0.02 * s, W * 0.36), trim, joints["pelvis"], (0, front, Hh * 0.62), 0.5, coll))
        objs.append(part_obj(f"{cid}.{tag}.collar", (W * 0.42, D * 0.6, Hh * 0.05), trim, joints["pelvis"], (0, 0, Hh * 0.99), 0.3, coll))
    elif style == "gown":
        for k in range(5):
            for j in range(4):
                objs.append(part_obj(f"{cid}.{tag}.dot{k}{j}", (0.06 * s, 0.015 * s, 0.06 * s), trim, joints["pelvis"], (-W * 0.36 + W * 0.18 * k, front, Hh * (0.15 + 0.22 * j)), 0.4, coll))
        objs.append(part_obj(f"{cid}.{tag}.collar", (W * 0.5, D * 0.7, Hh * 0.05), trim, joints["pelvis"], (0, 0, Hh * 0.99), 0.3, coll))
    sleeve = {"tee": 0.42, "gown": 0.4, "shirt": 0.6}.get(style, 0.8)
    for side in ("r", "l"):
        joint, ac, asz = body.part("arm_" + side)
        aw, ad, ah = asz
        top_z = ac[2] + ah / 2
        sl_h = ah * sleeve
        objs.append(part_obj(f"{cid}.{tag}.sleeve_{side}", (aw, ad, sl_h), tm, joints[joint], (0, 0, top_z - sl_h / 2), 0.1, coll))
        objs.append(part_obj(f"{cid}.{tag}.hand_{side}", (aw * 0.985, ad * 0.985, ah - sl_h), skin, joints[joint], (0, 0, top_z - sl_h - (ah - sl_h) / 2), 0.12, coll))
        if style in ("hoodie", "sweater", "cardigan"):
            objs.append(part_obj(f"{cid}.{tag}.cuff_{side}", (aw * 1.02, ad * 1.02, ah * 0.07), fabric("cuff", top["color"], 0.9, 200), joints[joint], (0, 0, top_z - sl_h + ah * 0.035), 0.2, coll))
        if style == "shirt":
            objs.append(part_obj(f"{cid}.{tag}.roll_{side}", (aw * 1.03, ad * 1.03, ah * 0.08), trim, joints[joint], (0, 0, top_z - sl_h + ah * 0.04), 0.3, coll))
        joint, lc, lsz = body.part("leg_" + side)
        lw, ld, lh = lsz
        shoe_h = lh * 0.16
        objs.append(part_obj(f"{cid}.{tag}.pants_{side}", (lw, ld, lh - shoe_h), pants, joints[joint], (0, 0, lc[2] + shoe_h / 2), 0.08, coll))
        objs.append(part_obj(f"{cid}.{tag}.shoe_{side}", (lw * 1.01, ld * 1.06, shoe_h), shoes, joints[joint], (0, -ld * 0.03, lc[2] - lh / 2 + shoe_h / 2), 0.25, coll))
        objs.append(part_obj(f"{cid}.{tag}.sole_{side}", (lw * 1.015, ld * 1.07, shoe_h * 0.25), fabric("sole", "#E9E4DA" if outfit.get("shoes", "#222") not in ("#F1F1F1", "#FFFFFF") else "#7A7A7A", 0.6), joints[joint], (0, -ld * 0.03, lc[2] - lh / 2 + shoe_h * 0.12), 0.3, coll))
    return objs


def build_character(cid, spec, performer, atlases, tex_dir, coll):
    body = performer.body
    look = spec["look"]
    s = body.s
    root = empty(f"{cid}.root", coll=coll)
    joints = {"root": root}
    for j in ORDER:
        parent, off = body.joint_offset(j)
        joints[j] = empty(f"{cid}.{j}", joints[parent], coll, off)
    joints["pelvis"].location = (0, 0, body.hip_height)
    # Head
    _, hc, hsz = body.part("head")
    R, H = HEAD_RADIUS * s, HEAD_HEIGHT * s
    bev = 0.2 * R
    prof = [(0.0001, -H / 2), (R - bev, -H / 2), (R - bev * 0.3, -H / 2 + bev * 0.3), (R, -H / 2 + bev)]
    prof += [(R, H / 2 - bev), (R - bev * 0.3, H / 2 - bev * 0.3), (R - bev, H / 2), (0.0001, H / 2)]
    head = new_obj(f"{cid}.head", lathe_mesh(f"{cid}.head", prof, 48, cap=False), joints["neck"], coll)
    head.location = hc
    detail = tex_dir / f"detail_{cid}.png"
    has_detail = faces.draw_detail(look, detail)
    head.data.materials.append(face_material(cid, rgb(look["skin"]), atlases, detail if has_detail else None))
    for k in ("eyes", "mouth", "extra", "gaze_x", "gaze_y"):
        head[k] = 0.0
    hair = look.get("hair")
    if hair:
        build_hair(cid, hair["style"], hair["color"], head, s, coll)
    if look.get("glasses"):
        build_glasses(cid, look["glasses"], head, s, coll)
    outfits = {}
    for name, outfit in look["outfits"].items():
        outfits[name] = build_outfit(cid, body, look, outfit, joints, coll, name)
    return dict(root=root, joints=joints, head=head, outfits=outfits)


# ===================================================================== badge
FONT_BOLD = None


def badge_objects(cid, values, coll):
    """One white-on-ink text pair per distinct counter value."""
    out = {}
    root = empty(f"{cid}.badge", coll=coll)
    fill = emissive("badge_fill", (1, 1, 1), 2.2) if "badge_fill" not in _MATS else _MATS["badge_fill"]
    _MATS["badge_fill"] = fill
    ink = principled("badge_ink", (0.02, 0.02, 0.03), 0.6) if "badge_ink" not in _MATS else _MATS["badge_ink"]
    _MATS["badge_ink"] = ink
    glow = emissive("badge_glow", rgb("#FFE08A"), 4.0, alpha=0.6) if "badge_glow" not in _MATS else _MATS["badge_glow"]
    _MATS["badge_glow"] = glow
    for v in values:
        objs = []
        for layer, mat, off, z in (("ink", ink, 0.018, 0.0), ("fill", fill, 0.0, 0.006)):
            cu = bpy.data.curves.new(f"{cid}.badge.{v}.{layer}", "FONT")
            cu.body = v
            cu.font = FONT_BOLD
            cu.align_x = "CENTER"
            cu.align_y = "CENTER"
            cu.size = 0.2
            cu.offset = off
            cu.extrude = 0.006
            o = new_obj(f"{cid}.badge.{v}.{layer}", cu, root, coll)
            o.location = (0, -z, 0)
            o.rotation_euler = (math.pi / 2, 0, 0)
            cu.materials.append(mat)
            objs.append(o)
        out[v] = objs
    return root, out


# ===================================================================== main build
def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--preset", default="preview")
    ap.add_argument("--out")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--frames")
    ap.add_argument("--stills")
    ap.add_argument("--fast-bake", action="store_true", help="bake every 2nd frame (boards)")
    a = ap.parse_args(argv)
    t_start = time.time()
    ep = Path(a.episode).resolve()
    sp = json.loads((ep / "screenplay.json").read_text())
    timing = L.load_timing(ep)
    plan = L.compile_plan(sp, timing)
    slug = ep.name
    out = Path(a.out).resolve() if a.out else ROOT / "renders" / slug / a.preset
    out.mkdir(parents=True, exist_ok=True)
    tex_dir = out / "textures"
    tex_dir.mkdir(exist_ok=True)
    print(f"[build] plan: {len(plan.shots)} shots, {plan.duration:.2f}s, scratch timing={plan.scratch_timing}", flush=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = sp["title"]
    global FONT_BOLD
    font_path, _ = fonts.find("extrabold")
    if font_path:
        FONT_BOLD = bpy.data.fonts.load(font_path)
    fps = plan.fps
    scene.render.fps = fps
    n_frames = plan.frame(plan.duration) - 1
    scene.frame_start, scene.frame_end = 1, n_frames
    atlases = faces.build_atlases(tex_dir)
    textures = draw_photos(tex_dir)

    # ---- sets
    for sid, st in plan.sets.items():
        coll = bpy.data.collections.new(f"SET {sid}")
        scene.collection.children.link(coll)
        build_set(st, coll, textures)

    # ---- characters
    chars = {}
    cast_coll = bpy.data.collections.new("CAST")
    scene.collection.children.link(cast_coll)
    for cid, spec in sp["cast"].items():
        chars[cid] = build_character(cid, spec, plan.stage.performers[cid], atlases, tex_dir, cast_coll)
    print(f"[build] assets ready in {time.time() - t_start:.1f}s", flush=True)

    # ---- bake performances
    frames = list(range(1, n_frames + 1))
    step = 2 if a.fast_bake else 1
    bake_frames = frames[::step] + ([frames[-1]] if frames[-1] % step else [])
    cams_by_shot = {}
    for cid, ch in chars.items():
        p = plan.stage.performers[cid]
        rows = {k: [] for k in ("root_loc", "root_rot", "pelvis_loc", "pelvis_rot", "neck", "arm_r", "arm_l", "leg_r", "leg_l", "face", "badge", "vis")}
        for f in bake_frames:
            t = (f - 1) / fps
            pose = p.pose(t)
            if pose is None or pose.get("where") is None:
                rows["root_loc"].append((-500.0, -500.0 - 5 * len(rows["root_loc"]) * 0, 0.0))
                rows["root_rot"].append((0, 0, 0))
                rows["pelvis_loc"].append((0, 0, p.body.hip_height))
                rows["pelvis_rot"].append((0, 0, 0))
                for j in ("neck", "arm_r", "arm_l", "leg_r", "leg_l"):
                    rows[j].append((0, 0, 0))
                rows["face"].append((0, 0, 0, 0, 0))
                rows["badge"].append(None)
                rows["vis"].append(False)
                continue
            rows["root_loc"].append(pose["root"])
            rows["root_rot"].append((pose["lean"][0], pose["lean"][1], pose["yaw"]))
            rows["pelvis_loc"].append(pose["pelvis"])
            rows["pelvis_rot"].append(pose["pelvis_rot"])
            for j in ("neck", "arm_r", "arm_l", "leg_r", "leg_l"):
                rows[j].append(tuple(pose.get(j, (0, 0, 0))))
            fc = pose["face"]
            rows["face"].append((fc["eyes"], fc["mouth"], fc["extra"], fc["gaze"][0], fc["gaze"][1]))
            rows["badge"].append((pose["badge"], pose["points"]["top"]) if pose["badge"] else None)
            rows["vis"].append(True)
        J = ch["joints"]
        bake_channels(J["root"], "location", bake_frames, rows["root_loc"])
        bake_channels(J["root"], "rotation_euler", bake_frames, rows["root_rot"])
        bake_channels(J["pelvis"], "location", bake_frames, rows["pelvis_loc"])
        bake_channels(J["pelvis"], "rotation_euler", bake_frames, rows["pelvis_rot"])
        for j in ("neck", "arm_r", "arm_l", "leg_r", "leg_l"):
            bake_channels(J[j], "rotation_euler", bake_frames, rows[j])
        head = ch["head"]
        for i, k in enumerate(("eyes", "mouth", "extra", "gaze_x", "gaze_y")):
            bake_channels(head, f'["{k}"]', bake_frames, [r[i] for r in rows["face"]], interp="CONSTANT" if i < 3 else "LINEAR")
        # Outfit visibility by scene.
        if len(ch["outfits"]) > 1:
            changes = {}
            for name in ch["outfits"]:
                seq = []
                last = None
                for (t, nm) in p.outfits:
                    vis = nm == name
                    if vis != last:
                        seq.append((plan.frame(t), vis))
                        last = vis
                for o in ch["outfits"][name]:
                    key_vis(o, seq, n_frames)
                    set_constant(o)
        # Badge
        if sp["cast"][cid].get("badge") and p.badges:
            values = sorted({v for _, v in p.badges})
            broot, bobjs = badge_objects(cid, values, cast_coll)
            ch["badge_root"], ch["badge_objs"] = broot, bobjs
            ch["badge_rows"] = rows["badge"]
            for v, objs in bobjs.items():
                seq, last = [], None
                for f, row in zip(bake_frames, rows["badge"]):
                    vis = bool(row) and row[0]["text"] == v
                    if vis != last:
                        seq.append((f, vis))
                        last = vis
                for o in objs:
                    key_vis(o, seq, n_frames)
                    set_constant(o)
        print(f"[build] baked {cid}", flush=True)

    # ---- cameras
    cam_coll = bpy.data.collections.new("CAMERAS")
    scene.collection.children.link(cam_coll)
    hide_rows = {}
    for shot in plan.shots:
        sol = shot["camera"]
        cd = bpy.data.cameras.new(shot["id"])
        cd.sensor_fit = "VERTICAL"
        cd.sensor_height = camlib.SENSOR
        cd.sensor_width = camlib.SENSOR
        cd.clip_start = 0.05
        cd.clip_end = 400
        co = new_obj(f"CAM {shot['id']}", cd, coll=cam_coll)
        f0, f1 = plan.frame(shot["t0"]), plan.frame(shot["t1"]) - 1
        keys = sol["frames"]
        hh = sol.get("handheld", 0.0)
        seed = sum(map(ord, shot["id"]))
        fr_list = list(range(f0, f1 + 1))
        locs, rots, lens = [], [], []
        for f in fr_list:
            t = (f - 1) / fps
            if len(keys) == 2:
                u = (t - keys[0][0]) / max(keys[1][0] - keys[0][0], 1e-6)
                u = max(0.0, min(1.0, u))
                u = u * u * (3 - 2 * u) if sol["move"] in ("push", "pull", "arc", "rise") else u
                loc = tuple(keys[0][1][i] + (keys[1][1][i] - keys[0][1][i]) * u for i in range(3))
                aim = tuple(keys[0][2][i] + (keys[1][2][i] - keys[0][2][i]) * u for i in range(3))
                ln = keys[0][3] + (keys[1][3] - keys[0][3]) * u
            else:
                k = min(keys, key=lambda kk: abs(kk[0] - t))
                loc, aim, ln = k[1], k[2], k[3]
            rot = camlib.look_rotation(loc, aim)
            if hh:
                from kit.mathx import noise1
                rot = (rot[0] + math.radians(0.45 * hh) * noise1(t * 0.7, seed), rot[1] + math.radians(0.25 * hh) * noise1(t * 0.6, seed + 7),
                       rot[2] + math.radians(0.55 * hh) * noise1(t * 0.65, seed + 3))
            locs.append(loc)
            rots.append(rot)
            lens.append(ln)
        bake_channels(co, "location", fr_list, locs)
        bake_channels(co, "rotation_euler", fr_list, rots)
        cd.lens = lens[0]
        if abs(lens[-1] - lens[0]) > 0.01:
            bake_channels(cd, "lens", fr_list, lens)
        # Depth of field on the subject's eyes.
        if sol.get("focus") and a.preset != "board":
            cd.dof.use_dof = True
            cd.dof.aperture_fstop = sol["fstop"]
            dists = []
            for f in fr_list:
                t = (f - 1) / fps
                pose = plan.stage.performers[sol["focus"]].pose(min(t, shot["t1"] - 0.01))
                e = pose["points"]["eyes"] if pose else locs[0]
                dists.append(max(0.1, (Vector(e) - Vector(locs[fr_list.index(f)])).length))
            bake_channels(cd, "dof.focus_distance", fr_list, dists)
        elif sol["size"] == "insert" and a.preset != "board":
            cd.dof.use_dof = True
            cd.dof.aperture_fstop = sol["fstop"]
            cd.dof.focus_distance = (Vector(keys[0][1]) - Vector(keys[0][2])).length
        m = scene.timeline_markers.new(shot["id"], frame=f0)
        m.camera = co
        cams_by_shot[shot["id"]] = (co, fr_list, locs)
        for name in sol["hide"]:
            hide_rows.setdefault(f"{shot['set']}/{name}", []).append((f0, f1))
    scene.camera = cams_by_shot[plan.shots[0]["id"]][0]

    # ---- wild walls
    for name, spans in hide_rows.items():
        o = bpy.data.objects.get(name)
        if not o:
            continue
        seq = [(1, True)]
        for f0, f1 in sorted(spans):
            seq.append((f0, False))
            seq.append((f1 + 1, True))
        key_vis(o, seq, n_frames)
        set_constant(o)

    # ---- badges follow heads and face the active camera
    for cid, ch in chars.items():
        if "badge_rows" not in ch:
            continue
        locs, rots, scales = [], [], []
        for f, row in zip(bake_frames, ch["badge_rows"]):
            shot = plan.shot_at((f - 1) / fps)
            co, fr_list, clocs = cams_by_shot[shot["id"]]
            cl = clocs[min(max(0, f - fr_list[0]), len(clocs) - 1)]
            if not row:
                locs.append((-500, -500, -50))
                rots.append((0, 0, 0))
                scales.append((1, 1, 1))
                continue
            b, top = row
            p = (top[0], top[1], top[2] + 0.2)
            d = Vector(cl) - Vector(p)
            yaw = math.atan2(d.x, -d.y)
            pitch = math.atan2(d.z, math.hypot(d.x, d.y))
            dist = d.length
            k = max(0.55, min(2.2, dist / 3.2)) * b["pop"]
            locs.append(p)
            rots.append((pitch * 0.8, 0, yaw))
            scales.append((k, k, k))
        r = ch["badge_root"]
        bake_channels(r, "location", bake_frames, locs)
        bake_channels(r, "rotation_euler", bake_frames, rots)
        bake_channels(r, "scale", bake_frames, scales)

    # ---- FX: ripples and badge sparkles
    fx_coll = bpy.data.collections.new("FX")
    scene.collection.children.link(fx_coll)
    fx_items = list(plan.fx)
    for cid, p in plan.stage.performers.items():
        for (t, kind, data) in p.events:
            if kind == "badge" and p.where(t):
                fx_items.append(dict(kind="sparkle", t=t, dur=0.6, at=cid + ".top", who=cid))
    for i, fx in enumerate(fx_items):
        f0 = plan.frame(fx["t"])
        n = int(fx["dur"] * fps)
        set_ = plan.sets[plan.shot_at(fx["t"])["set"]]
        at = fx["at"]
        pt = plan.point(at, set_, fx["t"]) if not isinstance(at, list) else set_.world(tuple(at))
        if fx["kind"] == "ripple":
            mat = emissive(f"ripple{i}", rgb("#9FE3FF"), 6.0, alpha=0.0)
            mix = [nd for nd in mat.node_tree.nodes if nd.type == "MIX_SHADER"][0]
            bm = bmesh.new()
            bmesh.ops.create_circle(bm, cap_ends=False, radius=1.0, segments=48)
            me = bpy.data.meshes.new(f"ripple{i}")
            bm.to_mesh(me)
            bm.free()
            o = new_obj(f"FX ripple {i}", me, coll=fx_coll)
            sk = o.modifiers.new("rim", "SKIN") if False else None
            cu = bpy.data.curves.new(f"ripplec{i}", "CURVE")
            cu.dimensions = "3D"
            spl = cu.splines.new("POLY")
            spl.points.add(48)
            for k in range(49):
                ang = 2 * math.pi * k / 48
                spl.points[k].co = (math.cos(ang) * 0.5, 0, math.sin(ang) * 0.5, 1)
            cu.bevel_depth = 0.018
            bpy.data.objects.remove(o)
            o = new_obj(f"FX ripple {i}", cu, coll=fx_coll)
            cu.materials.append(mat)
            o.location = pt
            # face the shot camera
            co, fr_list, clocs = cams_by_shot[plan.shot_at(fx["t"])["id"]]
            d = Vector(clocs[0]) - Vector(pt)
            o.rotation_euler = (0, 0, math.atan2(d.x, -d.y))
            # hex wall: a translucent plane too
            pl = new_obj(f"FX wall {i}", box_mesh(f"wall{i}", (1.4, 0.005, 1.6)), coll=fx_coll)
            wm = emissive(f"wallfx{i}", rgb("#BFEFFF"), 1.5, alpha=0.0)
            pl.data.materials.append(wm)
            pl.location = pt
            pl.rotation_euler = o.rotation_euler
            wmix = [nd for nd in wm.node_tree.nodes if nd.type == "MIX_SHADER"][0]
            for k in range(n + 1):
                u = k / n
                f = f0 + k
                for ob, sc in ((o, 0.2 + 1.6 * u), ):
                    ob.scale = (sc, sc, sc)
                    ob.keyframe_insert("scale", frame=f)
                mix.inputs[0].default_value = max(0.0, 1 - u) ** 1.5 * 0.95
                mix.inputs[0].keyframe_insert("default_value", frame=f)
                wmix.inputs[0].default_value = 0.35 * math.sin(math.pi * u) ** 0.7
                wmix.inputs[0].keyframe_insert("default_value", frame=f)
            for ob in (o, pl):
                key_vis(ob, [(1, False), (f0, True), (f0 + n + 1, False)], n_frames)
            mix.inputs[0].default_value = 0.0
            mix.inputs[0].keyframe_insert("default_value", frame=f0 - 1)
            mix.inputs[0].keyframe_insert("default_value", frame=f0 + n + 1)
            wmix.inputs[0].default_value = 0.0
            wmix.inputs[0].keyframe_insert("default_value", frame=f0 - 1)
            wmix.inputs[0].keyframe_insert("default_value", frame=f0 + n + 1)
        elif fx["kind"] == "sparkle":
            perf = plan.stage.performers[fx["who"]]
            mat = emissive(f"spark{i}", rgb("#FFE9A3"), 8.0, alpha=0.0)
            mix = [nd for nd in mat.node_tree.nodes if nd.type == "MIX_SHADER"][0]
            parts = []
            for k in range(8):
                ang = 2 * math.pi * k / 8
                ob = new_obj(f"FX spark {i}.{k}", box_mesh(f"spark{i}{k}", (0.025, 0.025, 0.025), 0.008), coll=fx_coll)
                ob.data.materials.append(mat)
                parts.append((ob, ang))
            for k in range(n + 1):
                u = k / n
                f = f0 + k
                t = fx["t"] + u * fx["dur"]
                pose = perf.pose(min(t, plan.shot_at(fx["t"])["t1"] - 0.01))
                top = pose["points"]["top"] if pose else pt
                for ob, ang in parts:
                    r = 0.12 + 0.35 * u
                    ob.location = (top[0] + math.cos(ang) * r, top[1] + math.sin(ang) * r * 0.4, top[2] + 0.2 + math.sin(ang) * r * 0.6)
                    ob.keyframe_insert("location", frame=f)
                mix.inputs[0].default_value = max(0.0, 1 - u) ** 1.2
                mix.inputs[0].keyframe_insert("default_value", frame=f)
            for ob, _ in parts:
                key_vis(ob, [(1, False), (f0, True), (f0 + n + 1, False)], n_frames)

    # ---- world and sun per shot
    world = bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    wn = world.node_tree.nodes
    bg = wn["Background"]
    sky = wn.new("ShaderNodeTexSky") if False else None
    grad_tc = wn.new("ShaderNodeTexCoord")
    sepw = wn.new("ShaderNodeSeparateXYZ")
    world.node_tree.links.new(grad_tc.outputs["Generated"], sepw.inputs[0])
    wr = wn.new("ShaderNodeValToRGB")
    world.node_tree.links.new(sepw.outputs["Z"], wr.inputs["Fac"])
    world.node_tree.links.new(wr.outputs["Color"], bg.inputs["Color"])
    sun_d = bpy.data.lights.new("Sun", "SUN")
    sun = new_obj("Sun", sun_d)
    sun_d.angle = math.radians(3)
    for shot in plan.shots:
        st = plan.sets[shot["set"]]
        tod = st.times.get(shot["time"]) or st.times[st.default_time]
        f0 = plan.frame(shot["t0"])
        hi, lo = tod["sky"]
        wr.color_ramp.elements[0].color = (*rgb(lo), 1)
        wr.color_ramp.elements[1].color = (*rgb(hi), 1)
        wr.color_ramp.elements[0].position = 0.0
        wr.color_ramp.elements[1].position = 0.42
        bg.inputs["Strength"].default_value = tod["strength"]
        for el in wr.color_ramp.elements:
            el.keyframe_insert("color", frame=f0)
        bg.inputs["Strength"].keyframe_insert("default_value", frame=f0)
        s_ = tod.get("sun")
        if s_:
            sun_d.energy = s_["power"]
            sun_d.color = rgb(s_["color"])
            el, az = math.radians(s_["elev"]), math.radians(s_["azim"])
            sun.rotation_euler = (math.pi / 2 - el, 0, az)
        else:
            sun_d.energy = 0.0
        sun_d.keyframe_insert("energy", frame=f0)
        sun_d.keyframe_insert("color", frame=f0)
        sun.keyframe_insert("rotation_euler", frame=f0)
    for idb in (world.node_tree, sun_d, sun):
        ad = getattr(idb, "animation_data", None)
        if ad and ad.action:
            for fc in fcurves(ad.action):
                for k in fc.keyframe_points:
                    k.interpolation = "CONSTANT"

    # ---- render settings
    pr = PRESETS[a.preset]
    r = scene.render
    r.engine = pr["engine"]
    r.resolution_x, r.resolution_y = pr["res"]
    r.resolution_percentage = 100
    r.image_settings.file_format = "PNG"
    r.use_overwrite = False
    r.use_placeholder = True
    r.filepath = str(out / "frames" / "f_")
    try:
        scene.view_settings.view_transform = "AgX"
        for look in ("AgX - Medium High Contrast", "Medium High Contrast", "AgX - Base Contrast"):
            try:
                scene.view_settings.look = look
                break
            except TypeError:
                continue
    except TypeError:
        pass
    if pr["engine"] == "BLENDER_EEVEE":
        e = scene.eevee
        e.taa_render_samples = pr["samples"]
        e.use_shadows = True
        if hasattr(e, "use_raytracing"):
            e.use_raytracing = a.preset == "final_hq"
        if hasattr(e, "use_fast_gi"):
            e.use_fast_gi = True
        if hasattr(e, "shadow_ray_count"):
            e.shadow_ray_count = 2 if a.preset == "final_hq" else 1
        r.film_transparent = False
        scene.render.use_motion_blur = False
    else:
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL"
        sh.show_shadows = True
        sh.show_cavity = True
        scene.display.render_aa = "8"
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "episode.blend"), compress=True)
    print(f"[build] saved {out / 'episode.blend'} in {time.time() - t_start:.1f}s", flush=True)
    (out / "plan.json").write_text(json.dumps(plan.summary(), indent=1))

    if a.stills:
        for f in [int(x) for x in a.stills.split(",")]:
            scene.frame_set(f)
            r.filepath = str(out / "stills" / f"{f:04d}.png")
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            print(f"[still] frame {f} in {time.time() - t0:.1f}s", flush=True)
        r.filepath = str(out / "frames" / "f_")
    if a.render:
        if a.frames:
            f0, f1 = map(int, a.frames.split("-"))
            scene.frame_start, scene.frame_end = f0, f1
        t0 = time.time()
        bpy.ops.render.render(animation=True)
        print(f"[render] frames {scene.frame_start}-{scene.frame_end} in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
