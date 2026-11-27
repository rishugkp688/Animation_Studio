# Agent guide: everything we know about building and running this studio

Read this first if you are a new agent (Claude, Codex or any other) on this project. It
explains how to behave, how the engine works inside (precisely enough to rebuild it from
nothing), every mistake we already made and how we fixed it, and the plan for rebuilding
the studio if the code is lost. The other three files hold the department detail:
[01-blender.md](01-blender.md), [02-sound.md](02-sound.md), [03-production.md](03-production.md).

---

## 1. The project in one paragraph

A local "animation studio" that turns a written screenplay into a finished, captioned,
mixed 60-second vertical film in the Roblox R6 style, for Instagram Reels, on one Linux
laptop (GTX 1650 4 GB, Blender 5.3, FFmpeg, offline TTS and ASR). One JSON screenplay per
film drives everything. A pure-Python engine (`scripts/kit/`) turns it into solved
performances and cameras, **checks every frame for collisions, sliding feet, blocked faces
and bad cuts before anything renders**, and then Blender only bakes and renders. Sound is
generated from the same plan (voices, foley spotted from animation events, procedural
score, −14 LUFS master). `scripts/studio.py` runs the departments in order with a
pass/fail gate between each.

## 2. Why it is built this way (the lessons behind the architecture)

Before this design, every film had its own 600–1,400-line Blender script. Problems:

- **Nothing was shared**, so every film re-invented walking, faces, cameras; quality never
  accumulated. → *One engine, one rig, one face system, one camera solver for every film.*
- **QC ran on rendered video**, so collisions, sliding feet and frozen actors were found
  after an hour of rendering, or never. 8 of 9 old films failed their own audit.
  → *Animation is computed in pure Python and checked frame by frame before rendering.*
  What QC measures is exactly what Blender bakes.
- **Stories were basic and single-location**. → *A story method, a story gate with hard
  numbers (beats, setups, locations, expressions, pacing), and a critic pass.*
- **Paperwork without effect** (long review ledgers). → *Gates that block, not documents
  that describe.*

Keep these principles if you change anything:

1. **Single source of truth**: the screenplay. Fix problems in the screenplay or in the
   kit, never in a saved `.blend`, a rendered frame or a mixed WAV.
2. **Data over code for content**: sets, cast looks, voices, actions and cameras are
   data. Code is the shared engine.
3. **Verify before you spend**: cheap checks (story lint, voice ASR, per-frame layout QC,
   Workbench boards) come before expensive ones (renders).
4. **Measure the delivered file**, not the intention.
5. **Pure-Python core** (`mathx`, `rig`, `acting`, `sets`, `geom`, `camera`, `layout`,
   `qc`, `story`, `captions`) must never `import bpy`, so it runs in tests, in the host
   Python, and inside Blender identically.

## 3. The rig (R6 in studs)

1 stud = 0.33 m × character scale. Facing −Y at yaw 0; right = −X, left = +X.

Parts (parent joint, centre offset from the joint, size), in studs:

| Part | Joint | Centre | Size (w, d, h) |
| --- | --- | --- | --- |
| torso | pelvis | (0, 0, 1.0) | (2.0 × build, 1.0, 2.0) |
| head | neck | (0, 0, 0.66) | (1.30, 1.22, 1.28); rendered as a rounded cylinder r 0.65, h 1.22 |
| arm_r / arm_l | arm joint | (0, 0, −0.55) | (0.94, 0.96, 2.0) |
| leg_r / leg_l | leg joint | (0, 0, −1.0) | (0.97, 0.98, 2.0) |

Joints (parent, offset), in studs: pelvis ← root (0, 0, 2.0); neck ← pelvis (0, 0, 2.0);
arm_r ← pelvis (−1.52 × build, 0, 1.55); arm_l (+1.52 × build, 0, 1.55); leg_r ← pelvis
(−0.5 × build, 0, 0); leg_l (+0.5 × build, 0, 0). Root sits on the floor under the hips;
its rotation is (lean x, lean y, yaw).

Derived: leg length 2s (s = 0.33 × scale), hip height 2s, height (4.04 + 1.22)s, eye
height 4.66s, lateral foot offset 0.5s × build.

Rotation conventions (Euler XYZ, same as Blender): positive X on a limb swings its free
end backward (+Y), so forward is negative; positive X on the neck tilts the face down;
positive Z yaw turns toward the character's left.

Named points (in joint space, ×s): eyes (0, −0.6, 0.70) and face (0, −0.66, 0.62) on the
neck; top (0, 0, 1.35); chest (0, −0.5, 1.45) and hips (0, 0, 0.1) on the pelvis; hands
(0, 0, −1.55) on the arms; feet (0, 0, −2.0) on the legs; shoulders (0, 0, 0.3) on the
arms; `face_dir` = neck's −Y. Everything that aims (looks, gestures, cameras, QC) uses
these points.

**Aiming a limb**: a limb hangs along its local −Z. To point it along direction d (in
the parent's space): `a = asin(d_y)`, `b = atan2(−d_x, −d_z)`, Euler = (a, b, 0).
Transform world targets into pelvis space with the transpose of the pelvis rotation.

**Forward kinematics**: world matrix of each joint = parent matrix × compose(offset,
euler). The matrix helpers (`mathx.compose`, `mat_mul`, `transform`) are written to match
`Matrix.LocRotScale(loc, Euler(rot, 'XYZ'), scale)` exactly.

**Collision volumes**: each part is an oriented box (OBB) from the joint matrices
(shrunk by 4 mm for QC). Heads are treated as cylinders: the box is re-oriented to face the
other object, so a turning head does not swing phantom corners into a partner.

## 4. The performance engine (`acting.py`)

A `Performer` holds timelines filled by the layout compiler: segments (place, walk, turn,
sit, stand_up, crouch), gestures, looks, faces, talks, badges, outfits, allowed contacts
and sound events. `pose(t)` returns the complete rig pose and face channels for any time.

### 4.1 Walking with planted feet (the most important algorithm)

Feet are planned as **world-space footprints**; a planted foot is a fixed point, and the
rigid leg is aimed at it, so stance feet *cannot* slide.

1. Path: the actor's position plus the route points (from navigation). Arc length table.
2. Speed profile: ease in and out (ramp 0.38 s walk / 0.28 s run, ≤ T/3), constant cruise
   between. `s(t)` = distance along the path.
3. Stride = leg × 0.72 (walk) / 1.15 (run). n steps = round(L / stride); D = L / n.
4. Footprint j (j = 1..n+1) alternates right/left, sits at path distance min(jD, L),
   offset sideways by the lateral foot offset using the smoothed heading (look-ahead
   ±0.35 m). The final pair is placed square at the end facing (`end_yaw`).
5. Foot j lifts when s reaches (j − 1.5)D and lands at (j − 0.5)D; in between it moves
   with smoothstep from the previous print to the new one with a lift arc
   (0.035 × scale walk, 0.07 run). Otherwise it is planted.
6. Heading: blends from the start yaw over 0.35 s into the path heading, and into the end
   yaw over the last 0.35 s. Walking backward adds π.
7. **Hip height from the legs** (inverted pendulum): for each planted foot, the hip can
   be at most sqrt(leg² − horizontal distance²) above it; use the minimum (never below half
   the leg). Add breathing (±2.5 mm) when standing.
8. **Rigid block feet dip at the corners** when a leg is angled: after aiming, compute the
   lowest corner of both leg boxes and lift the pelvis by any amount under the floor, then
   re-aim. (This removed a 7 cm dip.)
9. Arms swing opposite to the legs (amplitude 0.85 walk, 1.25 run), the body leans forward
   with speed (0.04 walk, 0.16 run), the pelvis twists slightly, the head counters the lean.
10. Every landing emits a `step`/`run_step` event at its exact time (found by bisection on
    s(t)): that is where foley comes from.

Walk duration if not given: L / (speed × (2.6 if run) × (0.82 + 0.18 × scale)) + ramp.

### 4.2 Turning, sitting, standing, crouching

- **Turn in place**: yaw eases over 0.55 s; the right foot steps in the first 55 %, the
  left in the last 55 %, each with a 3 cm lift. Emits a `shuffle` sound.
- **Sit**: travel to the seat first (ease-out over 70 % of the time), rotate to the seat
  yaw, then lower (sit weight smoothstep from 15 %). Pelvis goes to seat top + half a stud;
  legs aim forward (thighs horizontal); arms rest forward on the lap. Beds tilt the pelvis
  back. Emits `sit` at 80 %.
- **Stand up**: foot targets are taken from the destination; travel first, then rise
  (sit weight falls from 35 %). Doing it in the other order swept the legs through the bed.
- **Crouch**: hip height to 0.58 leg, legs bent forward, arms forward.

### 4.3 Gestures aim at real targets every frame

A gesture is a function `(ctx, local_time, duration, params) → {arm_r, arm_l, neck,
neck_set, gaze}` plus optional `lean`, `crouch` and declared `contacts`. The weight
envelope ramps up from the cue over `blend_in`, holds, and eases out over `blend_out`
after the end. Arms blend from the base pose to the gesture's targets by the weight.
Targets are resolved through other actors' `anchor()` points (base layer only, without
gestures or gaze, cached) to avoid infinite recursion when two actors aim at each other.

Key gestures:

- **hug**: hands aim just past the partner's flanks and behind their back:
  width = max(partner half-width + arm half-width + 3.5 cm, 0.95 × own shoulder span), back
  offset 0.6 studs, height `lift` above the target point. The taller actor holds `at: head`
  (partner half-width = head radius), the shorter `at: hips`. Arms stay nearly parallel so
  rigid blocks do not cross. The hug *owns the neck* (`neck_set`: pitch, turn) so the head
  rests on the partner. A small squeeze over 0.5 s, breathing. Declared contacts: arm–arm
  up to 13 cm, arm–body 8 cm, torso 5 cm, head 7 cm, own torso 7 cm.
- **open_arms**: arms raised forward (`raise` 1.25–1.5 rad) and spread; always before a hug
  closes, otherwise arms pass through the arriving partner.
- **hold_shoulders**: both hands on the partner's opposite shoulders + `lift`, head pitched down.
- **ruffle**: hand aims above the partner's head, offset toward the actor, wobbling at 2.5 Hz.
- **blocked_hug**: arms reach forward, stop at `stop` seconds with a small vibration,
  recoil, while the body leans in and back.
- **hands_face**, **sob**, **head_down**, **nod**, **shake_head**, **shrug**, **flinch**,
  **think**, **point**, **reach**, **wave**, **offer**, **hold_hand**, **hand_chest**,
  **look_around**, **lean_forward**, **hands_behind**, **crouch**.

Partner gestures (`hug`, `ruffle`, `hold_hand`, `hold_shoulders`) must name a partner
(`with`); compiling fails otherwise.

### 4.4 Looks: eyes lead the head

On a new look target, the pupils jump to it immediately (gaze offset up to 0.9/0.6), the
head turns over 0.38 s (smoothstep) and is clamped to ±1.15 rad yaw, −0.55..0.6 pitch;
the remaining angle stays in the eyes. A blink fires 20–130 ms after each look change.
Targets: another actor's eyes, a set point, a world point, or the shot camera (`cam`).

### 4.5 Faces and life

- Expressions map to (eye, mouth, extra) cells (see the table in `03-production.md`).
- **Blinks**: every 2.6–4.5 s (hashed per actor), 130 ms, plus on every expression change
  and big look change; a blink swaps the eye cell for its closed variant
  (sad → closed_sad, happy → closed_happy, ...).
- **Lip-sync**: from the take's envelope (see `02-sound.md` §2).
- **Idle life**: breathing (3.4 s period), slow weight sway, small head drift and nods,
  extra head motion while talking. Nobody is ever perfectly still.
- **Counters** (badges): text per value; a change pops the scale (+35 %, decaying ×9/s),
  sparkles and chimes.

## 5. The screenplay compiler (`layout.py`)

`compile_plan(screenplay, timing)`:

1. Load each set once, in first-use order, offset 70 m apart on X.
2. Create performers (seeded), parked in the green room.
3. For each scene: set outfits, place the cast (`at`/`yaw`, `mark`, `seat`, or `contact`
   = in front of another actor at embrace distance), starting expressions, looks, counters;
   everyone else goes to the green room.
4. For each shot: place its lines (§3 of `02-sound.md`), compute its duration, run its
   actions in order (time expressions, `last_end` per actor for `+x` timing).
5. Music cues → absolute times. Then solve one camera per shot and expose its position as
   the point `__cam__<shot>` (for "look at camera").

Embrace distance (`_contact_gap`): max(sum of half depths, shorter actor's head radius +
taller actor's half depth) + 7 cm if heights differ by > 15 cm + 3 cm.

Routes: grid A* (10 cm cells) over the set's walkable bounds, obstacles = footprints of
`nav` elements between 5 cm and 1.6 m high, **inflated by their half extents plus the
actor radius** (≈ 2.05 × s × build + 6 cm). The path is string-pulled (skip to the farthest
visible point). Endpoints are kept exactly; a straight line is used if it is free.

## 6. Sets as data (`sets.py`)

A `Set` has elements (`shape` box/cyl/cone/sphere/plane/text, `at` centre, `size`, `rot`/
`rx`/`ry` degrees, `mat` spec, `solid`, `nav`, `cut` inward normal, `bevel`, `tag`),
`marks` (x, y, yaw), `seats` (x, y, z top, yaw, kind chair/bed), `points` (look targets),
`lights`, walkable `bounds`, optional `camera_bounds`, `surface` (footsteps: concrete,
wood, vinyl), `reverb` (IR seconds, wet for dialogue), `times` (sky gradient colours,
strength, sun elevation/azimuth/colour/power), and `default_time`. Helpers:
`box(x0, y0, z0, x1, y1, z1)`, `wall_with_openings(axis, fixed, a0, a1, height, thickness,
openings=[(start, end, bottom, top)], cut=...)` which builds the solid pieces around doors
and windows, and dressing functions `tree`, `bush`, `window`, `door`, `chair`.

The same element list feeds the Blender build, collision QC, navigation and the camera
solver. That is why a set must be honest: if something collides it is `solid`; if it is
dressing it is not.

## 7. The camera solver for 9:16 (`camera.py`)

**Shot sizes are visible frame height in studs** of the subject (because a 9:16 frame is
width-limited and R6 heads are 1.3 studs wide): ECU 2.5, CU 3.4, MCU 4.7, MS 6.4, FS 8.6,
WS 14, EWS 24. Distance = (size × s / 2) / tan(vfov / 2) with a vertical sensor of 36 mm.
For close sizes the aim height puts the eyes at 55 % (ECU), 60 % (CU), 66 % (MCU), 72 %
(MS) of the frame height; wider sizes frame from the floor.

Placement: the camera sits at `side` degrees around the subject from their facing
(`relative: body` = root yaw, `face` = head direction, `world` + `world_yaw`), at
`height` elevation (eye 0°, slight_high 10°, high 24°, top 62°, slight_low −7°, low −14°,
worm −30°). Two-shots frame both actors' eye midpoint from the perpendicular of the line
between them, on the side they face. OTS sits behind and beside the first actor's head,
looking at the second actor's eyes. Inserts orbit a point.

**One framing per shot at its key moment** (`key`, default 60 % into the shot). Moves are
built around that same aim: push (distance ×1.14 → ×0.94), pull (×0.93 → ×1.13),
push_fast (×1.3 → ×0.92), drift (±3.5 % sideways), arc (orbit ±arc/2), rise (−22 cm → +12
cm), static. `track` re-frames every frame and smooths over ±6 frames. (Framing at several
times made the lens swing; one key framing fixed it.)

**Validation at 7 samples along the move** (clamped inside the shot, never on the cut
frame): the lens is not inside a solid element (12 cm margin) and inside the camera
bounds; every subject's eyes project inside the frame (4–96 % x, 5–97 % y); the line of
sight from lens to eyes is not blocked by a solid element or another actor (heads padded
for hair). Three samples missed occluders; seven did not.

**Search**: if the requested placement fails, try side offsets 0, ±12, ±24, ±36, ±50, ±65°,
elevation +0/10/20° and distance ×1.0/0.85/0.72, ordered by cost (|Δside| + 1.6 × Δelev +
60 × (1 − distance scale)); take the first clean one and log the adjustment. `locked`
disables the search.

**Wild walls**: any `cut` element whose inward normal shows the camera is outside it, or
that the lens-to-aim segment crosses, is hidden for that shot only.

DOF on the subject's eyes, handheld noise, and lenses per size are in `01-blender.md` §9.

## 8. Layout QC (`qc.py`) — how each check works

For every frame (24 per second, step 1):

- **collision.set**: each body part OBB vs each solid set element (AABB pre-filter, then
  separating-axis overlap depth on the 15 axes). Error if depth > 2.5 cm, unless sitting on
  a `seat`/`bed` element (< 6 cm) or a declared contact.
- **collision.actor**: every part pair of two actors in the same set within 3 m. Error if
  depth > 2.5 cm unless either actor declared that contact for that time (±0.45 s) with a
  max depth (parts matched with fnmatch globs, e.g. `arm_*` vs `mom:arm_*`).
- **collision.self** (warning): an arm > 20 cm inside its own head or torso.
- **feet.sink** (error): lowest leg corner > 3 cm below the floor while standing.
  **feet.hover** (warning): > 4 cm above with both feet planted.
- **feet.slide** (error): a foot planted in two consecutive frames moved > 2.2 cm
  horizontally (measured at the sole centre, not the lowest corner, which flips; moves
  > 1 m are teleports between scenes and ignored).
- Per shot: camera report messages (blocked/out-of-frame faces in close sizes and lens
  inside geometry are errors, other adjustments warnings); `face.caption` (eyes too low in
  close-ups, where captions sit at 24–40 % height); `cut.axis` (two actors swap screen order
  across a cut in the same scene); `cut.jump` (same size, subject and angle within 20°);
  `film.variety` (< 3 sizes, error).

Results are merged into frame ranges (rule, shot, message) with the maximum depth.

## 9. The rest of the engine

- `faces.py`: PIL drawing of 16 eye shapes, 16 mouths, 4 extras and pupils at 512 px per
  cell (2× supersampled), plus per-character detail (moustache, beard, wrinkles, freckles).
  Ink colour (34, 24, 26); tears (132, 200, 255, 225); mouth interior and tongue colours.
- `story.py`: the story gate (`03-production.md` §4.6).
- `voice.py`, `align.py`, `listen.py`, `sound.py`, `captions.py`: `02-sound.md`.
- `blender_build.py`: `01-blender.md`.
- `scripts/video_audit.py`: motion per frame = mean absolute luma difference between
  consecutive frames at 72×128 (FFmpeg `tblend=difference,signalstats`); a cut is a frame
  whose motion exceeds max(9, 5 × the median of the 6 frames each side), at least 0.25 s
  after the previous cut; frozen = motion < 0.05 for ≥ 0.9 s; static shot = mean motion
  < 0.18; also flashes, black frames, silent gaps, loudness. `judge()` splits results into
  hard failures (block delivery), artistic notes (need a review disposition) and frozen
  spans excused by a shot's `hold`.
- `scripts/studio.py`: department commands, gates, review checkpoints, reports in
  `renders/SLUG/reports/` (`status.json`, `history.jsonl`), per-stage input fingerprints
  (engine, config, assets, tools, upstream artifacts) so `status` says what is stale and
  why, the frame manifest, and the retrospective. See `05-gates-reviews-retros.md`.
- `kit/fonts.py`: finds the house font (Fira Sans, else Noto/DejaVu, else `fc-match`;
  `BLOCKY_FONT_*` overrides) for sheets, counters and captions.

- `kit/shotreport.py`: the text shot report (`reports/shots.txt`) written by the layout gate,
  for reviewers that cannot see images.
- `kit/repair.py`: bounded deterministic staging repair (`studio.py repair`).
- `kit/catalog.py`: the catalog and screenplay schema, read from the engine (`studio.py catalog`,
  `studio.py schema`); the story gate validates against the schema first.

Tests, no Blender needed. `python3 tests/test_kit.py` (39 tests, ~2.5 min): stance feet stay
planted while walking; feet never sink; every expression resolves; OBB overlap depth; sets
are R6 scale; gestures can target set points; sound and captions follow the screenplay's
narrator and speakers (no hard-coded cast); every set declares its sound; duration limits
come from the quality bar; a `hold` must say why; font fallback; and for **both films**
(One Hug Left, Three Words): story gate passes, no layout errors, close-up eye line,
narrator, duration within the limit; the style seam (`06-style-seam.md`); unknown names list
the valid ones; catalog and schema (both films validate); the shot report flags a hidden face;
repair is bounded, mechanical, exact to undo and fixes a start mark inside furniture; hug
defaults by height (Three Words passes with every hug value removed); a route to a blocked
point beside the actor is never empty. `python3 tests/test_studio.py` (46 tests): the
pipeline's gates, including invalid frames, mixes and frozen picture that must stop
delivery (`05-gates-reviews-retros.md` lists them), a board cannot be approved before layout
passes, and headless Blender gets the null audio driver. Add a test for every new behaviour;
add every finished film to `FILMS` in `test_kit.py`.

## 10. Bugs we already hit (do not repeat them)

| Problem | Cause | Fix |
| --- | --- | --- |
| Gate passed, nothing rendered | Blender exits 0 on script errors | `--python-exit-code 1` + check outputs |
| `import PIL` fails in Blender | pyenv Python on PATH | clean environment for Blender |
| Grey storyboards | Workbench uses `diffuse_color` | set it on every material |
| Hospital shots black | object names collided across sets | prefix names with the set id |
| Infinite recursion when two actors look at each other | look targets used full poses | `anchor()` from the base layer, cached |
| Feet dipped 7 cm through the floor | rigid leg corners | lift the pelvis by the lowest corner |
| Actors stuck on thin walls | nav inflated only by the radius | inflate by half extents + radius |
| Hugs interpenetrating | fixed gap and widths | embrace distance from both bodies; width from the partner; taller high/shorter low; open arms first; child starts first; release in order |
| Legs swept through the bed on stand-up | lowered before travelling | feet from the destination; travel, then rise |
| False foot slides | lowest corner flips between feet corners | measure the sole centre; ignore teleports |
| Validation failed on the cut frame | sampled at the shot edge | clamp samples inside the shot |
| Lens swung during a shot | framed at several times | one key-moment framing |
| Occluders missed | 3 samples | 7 samples |
| Close-ups cropped faces | sizes by body height, ignoring 9:16 width | sizes in studs of frame height |
| Close-ups aimed wrong | face-relative by default | body-relative by default |
| faster-whisper crashed | PyAV decoder | decode with FFmpeg to numpy |
| A child line misheard | TTS pronunciation | `say` text + new seed; match on the spoken text |
| "MOSHUNDREDS" in captions | overlapping ASS events | force events sequential |
| `-vsync` error | removed in FFmpeg 7 | `-fps_mode vfr` |
| 180° jumps, jump cut (sh15, sh18) | camera sides | change side; cut-in to ECU |
| Wait loop never ended | `pgrep` matched its own command line | wait on an output condition (frame count) |
| Hero chime, caption colours, reverb and footsteps only worked for One Hug Left | names and set ids hard-coded in sound/captions/layout | narrator from the screenplay; tints by speaker order; `surface`/`reverb` on each Set; tests in `Generality` |
| `point at "photo"` crashed at layout | gestures could only target actors | layout resolves set points/marks to world points when compiling |
| Story gate said 80 s, real film 67 s | word-count estimate too slow (2.55 words/s) | refit: 0.26 + 0.30 × words (3.3 words/s) |
| Hug between near-equal heights interpenetrated | hug assumes tall-high/short-low | keep scales ≥ 0.15 apart or use hold_shoulders |
| Wait loop never ended (again, 2026-11-27) | `pgrep -f PATTERN` inside a shell whose own command line contains PATTERN | wait on the background task's own exit, or an output file, never on `pgrep -f` |
| Headless Blender hung on exit in agent sandboxes | OpenAL blocks without an audio device | `ALSOFT_DRIVERS=null` in Blender's environment (`studio.clean_env`) |
| Walk crashed with `IndexError` | `Nav.route` returned `[]` when start and goal snapped to the same free cell with the line blocked | return `[goal]`; QC reports the collision instead |
| Longer legs (rig experiment) left walking on the old length | `Body` hard-coded leg/arm/height apart from `PARTS`/`JOINTS` | every dimension derived from the tables; `StyleSeam` tests |
| Hug gap sized from an R6 constant in layout | `layout` imported `rig.HEAD_RADIUS` | `Body.head_radius`; import-boundary test |
| Agent judged a storyboard built from staging that failed layout, blamed the engine | `board` did not check the layout gate | board warns; a board review cannot approve until layout passes |

## 11. Environment facts

- Host Python 3.14 via pyenv (no numpy/PIL): only for `studio.py`, the pure kit and tests.
- Blender's Python: system 3.14 with numpy and Pillow (faces, contact sheets).
- `.audio-tools/venv` (3.12): numpy, kokoro-onnx, onnxruntime. `.audio-tools/asr` (3.12):
  faster-whisper (`small.en` cached locally).
- Fonts: Fira Sans (Arch: `/usr/share/fonts/TTF/`), found by `kit/fonts.py`, which falls
  back to Noto Sans, DejaVu Sans or `fc-match`; set `BLOCKY_FONT_BOLD` etc. to override.
- Long renders: run in the background and wait on a real condition (number of PNGs
  equals the frame count), not on a process name.
- The reference reels in `guides/` are third-party videos: study them, never reuse them.

## 12. How to behave as an agent here

- **Wear one hat at a time**, in the order of `03-production.md` §2, and do not skip a gate.
  Write decisions and the director's notes in `episodes/SLUG/notes.md`.
- **When asked for "a story", write a real one** with the story method, then read it as
  the director and as a critic *before* building anything. A story that only passes the
  lint is not finished.
- **Look at your output.** Read the storyboard sheet, the contact sheet and a few single
  frames (hook, reveal, button) as images. Describe what you saw.
- **Be honest in reports.** Say what was measured and what was not. You cannot listen; say
  "nobody has listened to the mix by ear". You cannot watch on a phone; say so. Never claim a
  taste judgement a human has not made. Report failures with their numbers.
- **Fix the cause, in the right place**: the screenplay for staging and timing, the kit for
  engine behaviour (with a test), the set library for geometry. Never suppress a QC error by
  deleting it; declare a contact through the gesture that creates it if the contact is intended.
- **Do not post anywhere.** Publishing to Instagram needs the creator's explicit go-ahead.
- **Original work only**: original stories, characters and sets in a genre style; never a
  recreation of an existing game, film or reel.
- **Keep the creator's bar in mind**: they judge as a viewer. "Decent" is a failure.
  Multiple locations, varied movement and expressions, a story with a real turn and cost.

## 13. Rebuilding the studio from nothing

If the code is gone, rebuild in this order; each step has a test before the next.

1. **`mathx`**: vectors, 4×4 row-major matrices, Euler XYZ compose matching Blender,
   easing (smooth, smoother, ease_out, overshoot), angle lerp/wrap, deterministic hash and
   1-D value noise. Test: compose vs a hand-computed rotation.
2. **`rig`**: the tables in §3, `Body(scale, build)`, `joint_matrices(body, pose)`,
   `part_boxes`, `points`, `aim_euler`, `to_local`. Test: standing pose feet at z = 0, eye height.
3. **`geom`**: OBB from a set element, SAT overlap depth, AABB overlap, point-in-OBB,
   segment–OBB slab test, footprints, grid A* with inflation and string pulling. Test: two
   unit boxes overlapping by 0.1 report 0.1.
4. **`sets`**: the `Set` class and helpers, then one set (start with a living room). Test:
   doors ≥ 1.35 m, seats reachable, nothing solid at the marks.
5. **`acting`**: Walk (§4.1), Performer compile API and `pose(t)`, then turn/sit/stand, then
   looks and faces, then gestures one by one (open_arms, hug, hold_shoulders first). Test:
   planted feet do not move during a walk; feet never sink.
6. **`camera`**: sizes in studs, placement, moves, validation, search, wild walls. Test: a
   CU keeps the eyes in the upper part of the frame.
7. **`layout`**: screenplay → plan (§5). **`qc`**: §8. Test: the reference screenplay
   compiles with zero errors.
8. **`story`**: the gate. Test: the reference screenplay passes.
9. **`faces`** (PIL) and **`blender_build`** (`01-blender.md`): build, bake, `--stills`.
   Look at a CU and a WS of each set.
10. **`voice` / `align` / `listen` / `sound` / `captions`** (`02-sound.md`).
11. **`studio.py`**: the commands and gates (`03-production.md` §2), input fingerprints
    and staleness, the frame manifest, delivery checks, contact sheet, review checkpoints
    and the retrospective (`05-gates-reviews-retros.md`); **`video_audit.py`** and
    `studio/quality-bar.json`. Test it with `tests/test_studio.py`.
12. Write one screenplay with the method, run `make --preset preview` (it stops for the
    storyboard review), review, iterate, then `--preset final`; fill the retrospective.

## 14. Open problems and the best next improvements

- **First-second motion** is below the bar (0.65 vs 1.0): open the hook on movement.
- **Exterior light is flat**: lower, warmer sun; richer sidewalk dressing.
- **Spiky hair** reads as shards in extreme close-ups: broader rounded locks for children.
- **The tightest Grandpa ECU sits off-centre.**
- **Storyboards have no faces** (Workbench ignores the face shader): draw a simple face in
  board mode or use Eevee at low samples for boards.
- **Only 4 sets**: add classroom, bedroom, kitchen, street, park, bus stop.
- **Gestures with props** (giving an object, holding a letter) need a prop-attach system
  (parent a prop to a hand point with a declared contact).
- **Nobody has listened by ear or watched on a phone.** The creator must do it before posting.
- Longer films (2–3 min, episodes): the plan already handles any number of scenes; budget
  render time (~75 min per minute at final) and keep one set per 20–30 s.
