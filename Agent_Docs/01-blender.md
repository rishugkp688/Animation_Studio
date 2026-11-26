# Using Blender for Roblox-style story shorts (agent skill)

Read this before you touch Blender in this project. It covers how to run Blender with
no window, how the studio builds a whole episode from code, the exact recipes that work
in Blender 5.3, how to check that a render is correct, and the mistakes that already cost
us hours. A new agent who has never opened Blender should be able to rebuild
`scripts/kit/blender_build.py` from this file alone.

Machine: CachyOS Linux, GTX 1650 (4 GB VRAM), Blender 5.3 on PATH as `blender`,
FFmpeg 9 with libass. Everything runs headless; nobody clicks in the Blender UI.

---

## 1. The golden rules

1. **Blender is a renderer, not the source of truth.** The screenplay
   (`episodes/SLUG/screenplay.json`) plus the pure-Python engine (`scripts/kit/`) define
   every pose, camera and light. Blender only receives the solved result and renders it.
   Never fix anything by editing a saved `.blend`: the next build rebuilds it from scratch
   and silently undoes your fix.
2. **Compute in pure Python, bake into Blender.** All animation is computed outside
   Blender (in modules that never `import bpy`) so the same numbers can be checked by QC
   before rendering. The builder evaluates `pose(t)` for every frame and writes keyframes.
   What QC checked is exactly what renders.
3. **Always headless, always factory settings, always fail loudly:**

   ```sh
   blender -b --factory-startup --python-exit-code 1 -P script.py -- ARGS...
   ```

   - `-b` runs with no window.
   - `--factory-startup` ignores the user's preferences and add-ons, so the result is
     the same on every machine.
   - `--python-exit-code 1` makes Blender exit with code 1 when the script raises. Without
     it **Blender exits 0 even when your script crashed**, and a pipeline gate will pass by
     mistake. We hit exactly this once.
   - Arguments after `--` belong to your script: `sys.argv[sys.argv.index("--") + 1:]`.
4. **Clean environment.** Blender's embedded Python is the system Python 3.14 with its own
   `numpy` and `Pillow`. If a pyenv or venv Python is on `PATH` or `PYTHON*` variables are
   set, Blender picks up the wrong site-packages and `import PIL` fails. Launch Blender with
   an environment that drops every variable starting with `PYENV`, `_PYENV`, `PYTHON`,
   `VIRTUAL_ENV` and removes pyenv folders from `PATH` (`clean_env()` in `scripts/studio.py`).
5. **Check the actual output.** After a render, check that the PNG files exist and
   that their count matches the frame count, and look at a few of them. An exit code proves nothing about the picture.

---

## 2. Scene conventions (use these everywhere)

| Thing | Convention |
| --- | --- |
| Units | metres; 1 Roblox stud = 0.33 m at character scale 1 |
| Up axis | +Z; floor at z = 0 inside each set |
| Character facing | local −Y at yaw 0; character's right is −X, left is +X |
| Rotation | Euler XYZ in radians (Blender default) |
| Frame numbers | 1-based in Blender: frame = round(t × fps) + 1; fps = 24 |
| Resolution | 9:16 vertical: board 360×640, preview 540×960, final 1080×1920 |
| Camera sensor | `sensor_fit = "VERTICAL"`, `sensor_height = sensor_width = 36` mm |
| Colour | `rgb(hex)` converts sRGB hex to linear before giving it to a material |
| Object names | `"{set_id}/{element}"` for sets, `"{cid}.{part}"` for characters, `"CAM {shot}"` for cameras |

Every set in an episode lives in one Blender scene, side by side, 70 m apart on X
(`SET_SPACING = 70`). Actors not in the current scene wait in a "green room" far away
(x, y = −400, −400) or are moved off screen. This makes cuts between locations just camera
switches, and one `.blend` holds the whole film.

**Object names must be unique across sets.** Blender silently renames duplicates
(`wall.001`) and any code that later finds objects by name (wild-wall hiding, visibility
keys) grabs the wrong one. Our hospital frames rendered black for exactly this reason
until names were prefixed with the set id.

sRGB → linear:

```python
def rgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)
```

---

## 3. Build order of an episode (what `blender_build.py` does)

```
read screenplay + voice timing
→ compile the plan with the pure-Python kit (same function QC uses)
→ bpy.ops.wm.read_factory_settings(use_empty=True)       # empty scene, no default cube
→ fps, frame range, fonts
→ draw face atlases (PIL) and photo textures into renders/SLUG/<preset>/textures/
→ build every set into its own collection "SET <id>" (meshes, materials, lights)
→ build every character into "CAST" (empties as joints, box parts, head, hair, outfits)
→ bake every performer for every frame (root, pelvis, neck, arms, legs, face channels)
→ outfit visibility per scene, overhead counters (badges) per value
→ one camera per shot in "CAMERAS", baked per frame, with timeline markers to switch
→ wild-wall hide keys per shot
→ counters follow heads and face the camera
→ FX (ripples, sparkles)
→ world gradient + sun keyed per shot (time of day)
→ render settings for the preset → save episode.blend → write plan.json
→ optional: --stills F,F,F (single frames) or --render [--frames A-B] (animation)
```

CLI of the builder:

```sh
blender -b --factory-startup --python-exit-code 1 -P scripts/kit/blender_build.py -- \
    episodes/SLUG --preset board|preview|final|final_hq --out renders/SLUG/PRESET \
    [--stills 12,48,96] [--render] [--frames 100-200] [--fast-bake]
```

`--fast-bake` keys every second frame (boards only).

---

## 4. Geometry recipes

The Roblox look is made of **bevelled boxes** and **lathed (turned) shapes**. Build meshes
with `bmesh`, never with `bpy.ops.mesh.*` operators: operators depend on context and
selection and fail in background mode in confusing ways.

### Bevelled box (bodies, furniture, walls)

```python
import bpy, bmesh

def box_mesh(name, size, bevel=0.0, segs=3):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= size[0]; v.co.y *= size[1]; v.co.z *= size[2]
    if bevel > 0:
        b = min(bevel, min(size) * 0.45)
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=b, segments=segs, profile=0.5, affect="EDGES")
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = bevel > 0          # smooth only bevelled boxes
    return me

def new_obj(name, data, parent=None, coll=None):
    o = bpy.data.objects.new(name, data)
    (coll or bpy.context.scene.collection).objects.link(o)
    if parent: o.parent = parent
    return o
```

Body parts use a bevel of about 7–12 % of their smallest side. Small set pieces
(< 3 cm thick) get no bevel.

### Lathe (heads, lamps, cylinders, pots)

A profile of (radius, z) pairs from bottom to top, revolved around Z:

```python
import math
def lathe_mesh(name, profile, segments=40, cap=True):
    bm = bmesh.new(); rings = []
    for r, z in profile:
        rings.append([bm.verts.new((r * math.cos(2*math.pi*i/segments), r * math.sin(2*math.pi*i/segments), z))
                      for i in range(segments)])
    for a, b in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            bm.faces.new((a[i], a[j], b[j], b[i]))
    if cap:
        bm.faces.new(list(reversed(rings[0]))); bm.faces.new(rings[-1])
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons: p.use_smooth = True
    return me
```

The **R6 head** is a rounded cylinder: radius 0.65 studs, height 1.22 studs, bevel
0.2 × radius, 48 segments:

```python
R, H = 0.65 * s, 1.22 * s; bev = 0.2 * R
prof = [(0.0001, -H/2), (R - bev, -H/2), (R - bev*0.3, -H/2 + bev*0.3), (R, -H/2 + bev),
        (R, H/2 - bev), (R - bev*0.3, H/2 - bev*0.3), (R - bev, H/2), (0.0001, H/2)]
head = new_obj(f"{cid}.head", lathe_mesh(f"{cid}.head", prof, 48, cap=False), neck_empty, coll)
```

(Use 0.0001 instead of 0 for the centre radius so faces never collapse.)

### Text (signs, counters)

```python
cu = bpy.data.curves.new(name, "FONT")
cu.body = "214"; cu.align_x = "CENTER"; cu.align_y = "CENTER"
cu.size = 0.2; cu.extrude = 0.006; cu.font = bpy.data.fonts.load(fonts.find("extrabold")[0])  # kit/fonts.py
o = new_obj(name, cu, parent, coll); o.rotation_euler = (math.pi/2, 0, 0)   # stand the text up, facing -Y
```

For an outlined counter use two text objects: a dark "ink" one with `cu.offset = 0.018`
(thicker outline) behind, and a white emissive "fill" one 6 mm in front.

### Thin rims (glasses, ripples)

A poly curve with `bevel_depth` gives a round tube cheaply:

```python
cu = bpy.data.curves.new(name, "CURVE"); cu.dimensions = "3D"
sp = cu.splines.new("POLY"); sp.points.add(24)          # 25 points total
for i in range(25):
    a = 2 * math.pi * i / 24
    sp.points[i].co = (rx * math.cos(a), ry * math.sin(a), 0, 1)   # 4D: x, y, z, w
cu.bevel_depth = 0.014 * s
```

### Rigs: objects parented to empties, not armatures

Characters are **rigid blocks**, exactly like Roblox R6, so we do not use armatures or
skinning. Each joint is an empty; body parts are meshes parented to the joint empties:

```
cid.root (empty, on the floor; rotation = lean x/y + yaw z)
└── cid.pelvis (empty at hip height)          ← torso box sits on top of it
    ├── cid.neck (top of torso) → head mesh → hair, glasses
    ├── cid.arm_r / cid.arm_l (shoulder pivots) → sleeve box + hand box
    └── cid.leg_r / cid.leg_l (hip pivots) → pants box + shoe + sole
```

The joint offsets and part sizes are in studs (see `04-agent-guide.md` §3). The pure-Python
forward kinematics uses the same hierarchy and the same Euler convention as Blender, so a
pose computed outside Blender lands identically inside it.

---

## 5. Materials that read as Roblox at phone size

A material spec string `"kind:#hex[:extra]"` is turned into a cached material, so the
set library stays pure data. Use Principled BSDF plus a little procedural variation;
flat colours look like a 2006 game, heavy textures look noisy at 540 px.

```python
def principled(name, color, rough=0.5, metal=0.0, spec=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    m.diffuse_color = (*color, 1)            # IMPORTANT: Workbench boards show this colour
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if "Specular IOR Level" in b.inputs:     # name changed in Blender 4+
        b.inputs["Specular IOR Level"].default_value = spec
    return m
```

**Set `diffuse_color` on every material.** Workbench (used for storyboards) ignores node
trees and shows `diffuse_color`; without it every board is grey.

Variation: feed a Noise/Wave/Brick/Checker texture (Object coordinates, through a Mapping
node) into a ColorRamp between two shades of the base colour (×0.85–0.95 and ×1.03–1.1),
optionally into a Bump node (strength 0.04–0.3). Values that work:

| Kind | Roughness | Pattern |
| --- | --- | --- |
| plastic / paint / ceramic | 0.42 / 0.55 / 0.3 | noise scale 3, ±4 % |
| wall | 0.85 | noise scale 1.5, bump 0.04 |
| wallpaper | 0.8 | wave scale 14, mapping (1, 0.02, 1), **vertical** projection |
| floor_wood | 0.45 | brick scale 2, mapping (1, 7, 1) = planks, bump 0.15 |
| wood | 0.55 | wave scale 3, mapping (1, 0.15, 0.15) = grain |
| brick / roof | 0.8 | brick, **vertical** projection, bump 0.25 |
| concrete / asphalt | 0.85 / 0.9 | noise scale 6 detail 6, bump 0.08 |
| pavers | 0.8 | brick scale 1.25, bump 0.12 |
| floor_vinyl | 0.35 | checker scale 0.9, ±4 % |
| grass / leaf | 0.9 / 0.7 | noise scale 5–6, bump 0.25–0.3 |
| fabric (clothes) | 0.75–0.9 | noise scale 60–110 (fine weave), bump 0.05–0.08 |
| hair | 0.55 | noise scale 40 |
| skin | 0.42, specular 0.4 | none |
| glass | 0.08, specular 1 | none |
| emit / screen | emission | strength 4 by default |

**Vertical projection** (walls, bricks): object coordinates stretch patterns on walls
that are not axis-aligned. Instead build the texture vector from world position as
(x + y, z): combine `Geometry.Position` X+Y into X and Z into Y. Bricks then run along any
wall and stay upright.

**Emissive with fade** (FX, glows): Emission → Mix Shader with Transparent BSDF, set
`m.surface_render_method = "BLENDED"`, and key the Mix factor (`mix.inputs[0]`).

**Windows** are emissive planes with a vertical gradient (sky colours, soft shapes) placed
just outside the wall opening. They read as daylight or night without lighting a real
exterior.

---

## 6. Faces: decals on a cylinder (the most important look decision)

Roblox faces are printed decals. We draw them once with PIL into **4×4 atlases** (16
cells of 512 px): eyes, mouths, extras (tears, tear drop, blush) and one pupil image.
The head's material picks a cell per frame from **object custom properties**, so an
expression change is a keyed number, not a material swap.

Shader recipe (all inside the head material):

1. Texture coordinate `Generated` → Separate XYZ.
2. Cylindrical UV: `u = atan2(x − 0.5, 0.5 − y) / 2.1 + 0.5`, `v = z`.
   (Dividing by 2.1 instead of 2π makes one cell cover about ±60° of the head front.)
3. For an atlas lookup with cell index `i` on a 4×4 grid:
   `col = i mod 4`, `row = floor(i / 4)`,
   `U = (u + col) / 4`, `V = (v + (3 − row)) / 4`. Image extension `CLIP`,
   interpolation `Cubic`. Mask alpha to 0 outside 0.003 < u, v < 0.997 so neighbours never bleed.
4. Read the index from `ShaderNodeAttribute` with `attribute_type = "OBJECT"` and
   `attribute_name = "eyes"` (or `mouth`, `extra`).
5. Layer with Mix (RGBA) nodes: skin → extras → eyes → pupils → mouth → per-character
   detail (moustache, beard, wrinkles, freckles).
6. **Pupils move**: offset the pupil lookup by `gaze_x × 0.032` and `gaze_y × 0.03`
   (custom properties) and multiply its alpha by "eye white" (eye texture red > 0.6 AND
   eye alpha). Pupils then slide inside the white and never draw on skin.

Custom properties are created on the head and keyed every frame:

```python
for k in ("eyes", "mouth", "extra", "gaze_x", "gaze_y"):
    head[k] = 0.0
bake_channels(head, '["eyes"]', frames, eye_values, interp="CONSTANT")    # indices snap
bake_channels(head, '["gaze_x"]', frames, gx_values, interp="LINEAR")     # gaze glides
```

Atlas geometry constants: eye centre v = 0.565, eyes ±0.128 in u from centre, mouth
v = 0.355, and u is scaled by 0.894 per v unit so circles stay round on the cylinder.
Eye whites must be **pure white** (the shader uses the red channel as the pupil mask).

---

## 7. Hair, clothes and props (character design in code)

- **Hair** is a set of chunky pieces parented to the head: a lathed dome "cap", an
  optional back panel (Solidify modifier for thickness), and locks (four-sided pyramids
  built with bmesh: four base vertices + a tip). Styles: `spiky`, `short`, `bob`,
  `long` (13 lock blades), `pigtails`, `bun`, `bald_sides`. **Fringes must stop above the
  brows**: the eyes carry the acting. Known issue: spiky locks look like sharp shards in
  extreme close-ups; prefer fewer, broader, rounded locks for children.
- **Outfits** are boxes on the same joints: the torso box in the top colour, plus trims
  (hoodie pocket, strings, hood and hem; cardigan inner shirt, buttons, pockets; shirt
  placket, collar, chest pocket, belt; sweater hem, collar, stripe; tee print; gown dots).
  Each arm is a sleeve box (top colour, 40–80 % of the arm length by style) plus a
  skin-coloured hand box; cuffs on long sleeves. Each leg is a pants box plus a shoe box
  (16 % of leg height, 6 % deeper than the leg) plus a light sole.
- **Several outfits per character** are all built; visibility is keyed per scene
  (`hide_render` and `hide_viewport` with CONSTANT interpolation).
- **Glasses**: two curve rims at the eye height on the head's surface (angle ±EYE_DU×2.1
  around the cylinder) plus a bridge. Keep them small: big frames hide the eyes.
- Give every lead **one colour nobody else wears** so they can be found in a wide shot.

---

## 8. Animation: baking thousands of keys fast

Inserting keys with `keyframe_insert` one by one is far too slow for 1,500 frames × 7
actors × 12 channels. Create the F-curve once, add all points, and fill them with
`foreach_set`:

```python
def fcurves(action):
    # Blender 4.4+/5.x "layered actions": curves live in layers → strips → channelbags.
    if hasattr(action, "fcurves") and len(getattr(action, "fcurves", [])):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out.extend(bag.fcurves)
    return out

def bake_channels(obj, data_path, frames, values, interp="LINEAR"):
    if not obj.animation_data: obj.animation_data_create()
    if not obj.animation_data.action:
        obj.animation_data.action = bpy.data.actions.new(obj.name + "Action")
    act = obj.animation_data.action
    n = len(values[0]) if isinstance(values[0], (tuple, list)) else 1
    for i in range(n):
        idx = i if n > 1 else 0
        fc = None
        try:
            fc = act.fcurve_ensure_for_datablock(obj, data_path, index=idx)
        except Exception:
            fc = None
        if fc is None:   # fallback: create the curve with one key, then clear it
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
```

Rules:

- **Key every frame** of the solved motion with LINEAR interpolation. Do not leave Bezier
  easing to Blender: the motion was already eased in Python and Bezier would overshoot
  between frames.
- Discrete things (face cell indices, visibility, sun per shot) use **CONSTANT**.
- Visibility: key `hide_render` and `hide_viewport` together, then set all those keys to
  CONSTANT (otherwise they interpolate as 0.5 = visible).
- `action.fcurves` no longer exists on new actions in Blender 5 (layered actions). Always go
  through the helper above.
- Keys on material node sockets (`mix.inputs[0].keyframe_insert("default_value", frame=f)`)
  and on lights (`light.keyframe_insert("energy", frame=f)`) work the same way.
- An actor who is not in the current scene is parked far away (−500, −500) rather than
  hidden, which keeps the channels simple.

---

## 9. Cameras for 9:16, one per shot

- Create one camera object per shot (`CAM sh07`), bake its location and rotation for
  the shot's frames, and add a **timeline marker** at the shot's first frame with
  `marker.camera = cam_obj`. Blender switches the active camera at each marker during an
  animation render. Set `scene.camera` to the first shot's camera.
- `cd.sensor_fit = "VERTICAL"`, sensor 36 mm, clip 0.05–400 m.
- Lenses: ECU 75 mm, CU 58, MCU 48, MS 38, FS 30, WS 24, EWS 20, insert 55, OTS 40, two-shot 35.
- Rotation that looks from `loc` at `aim`:

  ```python
  def look_rotation(loc, aim):
      d = (aim[0]-loc[0], aim[1]-loc[1], aim[2]-loc[2])
      return (math.atan2(math.hypot(d[0], d[1]), -d[2]), 0.0, math.atan2(-d[0], d[1]))
  ```

- **Depth of field** on the subject's eyes: `cd.dof.use_dof = True`, f-stop 2.0 (ECU),
  2.2 (CU), 2.8 (MCU), 3.5 (MS), 5.6 (FS), 8 (WS); key `dof.focus_distance` per frame as
  the distance from the lens to the subject's eye point. Turn DOF off for boards.
- **Handheld life**: add small noise to the rotation (≈0.45°, 0.25°, 0.55° on X, Y, Z,
  scaled by `handheld` 0–1, seeded per shot). Static shots get none.
- **Wild walls**: walls and ceilings flagged `cut` are hidden (visibility keys) only for
  the frames of shots whose camera sits outside them or whose line of sight crosses them.
- The camera *placement* (where the lens goes and why) is solved in pure Python
  (`kit/camera.py`) — see `04-agent-guide.md` §7. Blender only receives the keys.

---

## 10. Light, world and colour

- **View transform AgX** with the look `"AgX - Medium High Contrast"` (try
  `"Medium High Contrast"` and `"AgX - Base Contrast"` as fallbacks; the names change
  between versions, setting an unknown one raises `TypeError`).
- **World**: a vertical gradient (Texture Coordinate Generated → Separate Z → ColorRamp
  with stops at 0.0 and 0.42 → Background). Colours and strength come from the set's
  time-of-day preset and are keyed at each shot's first frame (CONSTANT), so each
  location has its own sky in the same file.
- **Sun**: one sun lamp, angle 3°, energy/colour/rotation keyed per shot from the set's
  `sun` (elevation, azimuth, colour, power; power 0 for night). Rotation:
  `(π/2 − elevation, 0, azimuth)`.
- **Set lights** are local: area lights (60–150 W, size 1–2 m) for ceilings and windows,
  point lights (radius 0.1 m) in lamps. Set `use_custom_distance = True,
  cutoff_distance = 14` so a light in one set never leaks into the set 70 m away.
- **Night interiors need a key light near the action** or faces go black.
- Exteriors: a low warm sun reads better than a high white one (our school-gate exterior
  is still too flat).

---

## 11. Render presets (GTX 1650)

| Preset | Engine | Size | Settings | Speed |
| --- | --- | --- | --- | --- |
| board | `BLENDER_WORKBENCH` | 360×640 | studio light, material colour, shadows, cavity, AA 8 | ~0.1 s/frame |
| preview | `BLENDER_EEVEE` | 540×960 | 24 TAA samples, shadows, fast GI | ~0.4–0.5 s/frame |
| final | `BLENDER_EEVEE` | 1080×1920 | 32 samples | ~3 s/frame (~75 min per film minute) |
| final_hq | `BLENDER_EEVEE` | 1080×1920 | 64 samples, ray tracing, 2 shadow rays | ~9 s/frame |

```python
r = scene.render
r.engine = "BLENDER_EEVEE"            # Blender 5 name; "BLENDER_EEVEE_NEXT" existed in 4.2–4.x
r.resolution_x, r.resolution_y = 1080, 1920
r.resolution_percentage = 100
r.image_settings.file_format = "PNG"
r.use_overwrite = False               # resumable: existing frames are skipped
r.use_placeholder = True              # several processes could share a range
r.filepath = str(out / "frames" / "f_")   # → f_0001.png ...
e = scene.eevee
e.taa_render_samples = 32
e.use_shadows = True
if hasattr(e, "use_raytracing"): e.use_raytracing = False
if hasattr(e, "use_fast_gi"): e.use_fast_gi = True
scene.render.use_motion_blur = False
```

- The Workbench engine id is **`BLENDER_WORKBENCH`** (not `WORKBENCH`). Setting a wrong
  engine name raises in the script, which with `--python-exit-code 1` stops the build.
- Use `hasattr` around Eevee options: they move between versions.
- Render the animation with `bpy.ops.render.render(animation=True)` after setting
  `frame_start`/`frame_end` (`--frames A-B` for a range). Single stills:
  `scene.frame_set(f); r.filepath = ".../stills/0042.png"; bpy.ops.render.render(write_still=True)`.
- An interrupted render resumes. **Delete the frames of shots you changed** before
  re-rendering, or the old frames stay.
- Save the scene for inspection: `bpy.ops.wm.save_as_mainfile(filepath=..., compress=True)`.

Budget before a final: render one heavy frame with `--stills` and multiply.

---

## 12. Encoding and checking the result

Frames → H.264 with burned captions and the mix:

```sh
ffmpeg -y -framerate 24 -start_number 1 -i frames/f_%04d.png -i sound/mix.wav \
  -vf "subtitles=captions.ass:fontsdir=/usr/share/fonts/TTF" \   # the folder kit/fonts.py found
  -c:v libx264 -preset slow -crf 17 -pix_fmt yuv420p -profile:v high -movflags +faststart \
  -c:a aac -b:a 192k -ar 48000 -shortest out.mp4
```

Always check the file you made, not the intention:

```sh
ffprobe -v error -show_streams -show_format -of json out.mp4          # codec, size, fps, duration
ffmpeg -v error -i out.mp4 -f null -                                   # decodes to the end? (no output = ok)
ffmpeg -hide_banner -i out.mp4 -af ebur128=peak=true -f null -         # integrated LUFS, true peak
ffmpeg -y -i out.mp4 -vf "select='between(t\,10.5\,10.54)+between(t\,20.1\,20.14)',scale=180:320,tile=8x3" \
       -frames:v 1 -fps_mode vfr frames.jpg                            # contact sheet, one frame per shot
ffmpeg -y -ss 45 -i out.mp4 -frames:v 1 check.png                      # one frame to look at
```

FFmpeg 7+ removed `-vsync`; use `-fps_mode vfr`. Then **look at the images** (an agent
can read PNG/JPG files): faces visible, nothing inside furniture, captions clear of faces,
counters readable, light plausible.

---

## 13. Debugging checklist

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Gate passed but no frames | Script crashed, Blender exited 0 | Add `--python-exit-code 1`; check the output files exist |
| `ModuleNotFoundError: PIL` inside Blender | pyenv/venv Python on PATH | Launch with the clean environment |
| Storyboards all grey | Workbench uses `diffuse_color` | Set `m.diffuse_color` on every material |
| Some shots black, others fine | Duplicate object names across sets | Prefix names with the set id |
| Light from another set appears | Unlimited light distance | `cutoff_distance = 14` |
| `AttributeError: 'Action' object has no attribute 'fcurves'` | Blender 5 layered actions | Use the `fcurves()` helper / `fcurve_ensure_for_datablock` |
| Visibility flickers or half-shows | Bezier/linear keys on hide flags | Set those keys to CONSTANT |
| Pupils float on the skin | Pupil alpha not masked by eye whites | Multiply by (eye red > 0.6) × eye alpha |
| Faces smeared across neighbouring cells | Atlas lookup bleeding | Clip mask 0.003–0.997, extension CLIP |
| Camera inside a wall | Wall not flagged `cut`, or the solver had no clear angle | Flag the wall or change the shot's side/size |
| Render slow | Ray tracing on, too many samples, huge textures | Use `final` (32 samples), keep atlases at 2048² |
| Look name error | AgX look names differ per version | Try the fallback names in a loop |

## 14. Inspecting a saved scene

```sh
blender -b renders/SLUG/final/episode.blend --python-expr "
import bpy; s = bpy.context.scene
print(s.render.engine, s.frame_start, s.frame_end, len(bpy.data.objects))
for m in s.timeline_markers: print(m.frame, m.name, m.camera.name if m.camera else None)
"
```

Use this to answer questions such as "which camera is active at frame 600" or "is that
object hidden in shot 12", but never fix anything there: fix the screenplay or the kit and rebuild.
