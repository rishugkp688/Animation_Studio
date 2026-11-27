# The style seam: what is Roblox R6 and what is not

An audit of how tightly the engine is tied to the Roblox R6 look, written before any second
style exists. It covers coupling between modules (what crosses a boundary and who depends
on it), not just where R6 code sits. It ends with what the tests enforce today and what a
second style would have to provide. **No style-pack architecture is built yet**; this is the
map for when one is wanted.

## Summary

- **Already style-neutral:** story gate, voice, alignment, listening, sound and mix,
  captions, delivery, the studio pipeline, staleness and reviews, the set library's
  geometry, geometric primitives. About half the engine.
- **The R6 implementation:** `rig.py` (body), `acting.py` (motion), `faces.py` (face
  decals) and the character half of `blender_build.py` (meshes, hair, outfits, materials).
- **The seam that exists:** shared code asks the rig for a `Body` (dimensions), posed
  geometry (`part_boxes`), named points (`points`: eyes, hands, feet, hips, chest...)
  and the vocabulary (`EXPRESSIONS`, `GESTURES`). That is a sound boundary, and it is now
  enforced by a test.
- **What leaks across it:** limb topology (one rigid segment per limb), proportions tuned
  in studs in shared code (camera framing, seat heights), the face-decal atlas in the plan,
  R6 contact tuning in the screenplay, and absolute staging coordinates written for R6 bodies.
- **Measured:** lengthening the legs through the rig tables first broke 12 checks in the
  two films because `Body` hard-coded its own copy of the leg length. That is fixed (`Body` now
  derives everything from the tables). The remaining 8 errors are seated legs, which on a
  knee-less rig stick straight out into space the screenplays staged for R6 legs.

## Modules by role

| Module | Role | Depends on the R6 body through |
| --- | --- | --- |
| `story.py` | neutral (reel conventions, not style) | vocabulary names only |
| `voice.py`, `align.py`, `listen.py`, `sound.py`, `captions.py`, `fonts.py` | neutral | animation **events** (footfalls, hug cloth) carried in the plan |
| `sets.py`, `geom.py`, `mathx.py` | neutral, with one calibration (seat heights) | `geom` uses `rig.OBB` as a box type |
| `layout.py` | neutral orchestration; compiles the screenplay to a plan | `Performer`, `Stage`, vocabulary, `Body` dimensions |
| `camera.py` | neutral solver, R6-calibrated framing table | `part_boxes` for occluders, `points` for eyes; `SIZE_STUDS` |
| `qc.py` | neutral checks, R6-tuned tolerances | `part_boxes`, `points`, contact allowances from acting |
| `rig.py` | **style**: R6 body | — |
| `acting.py` | **style**: R6 motion (walk, sit, gestures, looks, lip-sync, idle) | `rig` internals |
| `faces.py` | **style**: decal face atlases | `acting.EYES/MOUTHS/EXTRAS` |
| `blender_build.py` | **mixed**: sets, lights, cameras, counters, FX (neutral) + characters, hair, outfits, plastic materials (style) | `PARTS`, `JOINTS`, `ORDER`, head size |

## Coupling that crosses module boundaries

Each item: the assumption, where it lives, who relies on it, and what a different style
would break.

### 1. Limb topology: six joints, one rigid segment per limb

- **Where:** `rig.ORDER` / `JOINTS` (pelvis, neck, two shoulders, two hips). A pose is one
  Euler rotation per joint. No elbows, knees, wrists, spine or fingers.
- **Relied on by:** every gesture in `acting.py` aims a whole arm with `aim_euler` (one
  segment reaches a target by rotation alone); walking plants a foot by aiming a straight
  leg; sitting rotates straight legs 90° forward; the plan stores per-joint channels;
  `blender_build.py` keys exactly those channels on objects of the same names; QC and
  the camera read one box per part.
- **Breaks for:** any skeletal style. A two-bone limb needs IK (elbow/knee placement), a
  different sit, different contact allowances, and a plan format with more channels.
- **Seam today:** not abstracted. The plan format *is* the R6 joint list.

### 2. Proportions: stud measurements outside the rig

- **Where (fixed):** `Body` used to hard-code leg, arm, height and eye height separately from
  `PARTS`/`JOINTS`, and `points` hard-coded hand and foot offsets. Now derived from the tables.
- **Where (still):** `camera.SIZE_STUDS` / `EYE_AT` (how much of a subject each shot size
  shows) are calibrated for the R6 head, which is wide (1.3 studs) relative to the body;
  `acting.g_hug` repeats the shoulder offset (1.52 studs) and arm half-width; gesture
  targets use stud offsets (`hands_face`, `think`, `ruffle`); `Walk` stride is a fraction
  of leg length (fine); `sets.chair(seat_h=0.46)` and other seat heights fit an R6 hip
  height of 0.66 m at scale 1.
- **Breaks for:** a style with a smaller head or longer legs. QC still passes (the small-head
  experiment produced 0 errors), but close-ups frame differently. Only the visual review
  would catch it.

### 3. Collision geometry and tolerances

- **Where:** collision volumes are the rig's boxes (`part_boxes`); `qc.BODY_TOL` (2.5 cm,
  "bevels and rounding") and per-gesture allowed contact depths (`hug_contacts` etc. in
  `acting.py`) are tuned to rigid boxes.
- **Breaks for:** deforming meshes (capsules or per-bone hulls, different tolerances) and
  for styles where limbs bend around partners in a hug.

### 4. Expressions: vocabulary and implementation in one table

- **Where:** `acting.EXPRESSIONS` maps a name (`tender`, `teary`...) to an (eyes, mouth,
  extra) triple of **decal atlas indices**; `faces.py` draws the atlases in the same
  order; the plan stores `face_code` indices; `story.py` validates names against the same
  table.
- **Neutral part:** the names. A screenplay saying `"expr": "teary"` is style-free.
- **Breaks for:** any face that is not a texture swap (shape keys, a jaw bone, 2D
  replacement drawings). The plan should carry the name, and the style turns it into a
  face. Lip-sync is also atlas-based (`talk_a/e/o` mouths).

### 5. Contact and staging vocabulary in the screenplay

- **Where:** hugs take `at: hips|head|chest`, `lift`, `head_turn`, `head_pitch`, `lean`,
  `depth`, which are R6 tuning values; cast takes `scale` and `build`; walks and start marks use
  absolute coordinates in metres chosen around R6 bodies.
- **Measured:** with longer legs, seated characters' legs reached into walk paths and hug
  positions the screenplays had staged for R6 leg length (8 collisions over two films).
- **Neutral part:** the verbs (`hug`, `walk`, `approach`, `sit`, `look`, `point`), targets by
  name (actors, set points, seats) and timing.

### 6. The render build mixes neutral and style work

- `blender_build.py` (1,450 lines) builds sets, lights, cameras, counters/badges and FX
  (style-neutral given a Blender backend) in the same file as R6 characters, hair,
  outfits, face materials and the plastic look.

### 7. Sound follows the plan's events (a good seam)

- Footfalls, hug cloth and counter ticks come from events the motion code emits, so sound
  is independent of how a body looks, as long as the motion emits the same events.

## The seam contract (what a second style must provide)

Shared code may use only these, and `tests/test_kit.py::StyleSeam` fails if a non-style
module imports anything else from the style modules:

| Interface | Today (R6) | A new style provides |
| --- | --- | --- |
| `Body(scale, build)` dimensions: `leg`, `arm`, `hip_height`, `height`, `eye_height`, `lateral`, `half_depth`, `head_radius`, `s` | derived from `PARTS`/`JOINTS` | its own measurements |
| posed collision volumes: `part_boxes(body, mats)` | one box per part | volumes per body part, with matching tolerances |
| named points: `points(body, mats)` → head, eyes, face, top, chest, hips, shoulders, hands, feet, face direction | from joint matrices | the same names |
| vocabulary: `EXPRESSIONS`, `GESTURES` | decal triples, rigid-arm aims | the same names, its own implementation |
| motion: `Performer`, `Stage` | rigid R6 acting | walking, sitting, gestures, looks for its body |
| events in the plan | footfalls, hug cloth | the same events |
| character build in the backend | boxes, hair, outfits, decals | its meshes, rig and materials |

Not yet behind the seam (would need work before a second style): the plan's per-joint
channel format (1), framing calibration (2), collision tolerances (3), face codes in the plan
(4), and R6 tuning values in screenplays (5).

## What is enforced now

- `StyleSeam.test_body_dimensions_come_only_from_the_rig_tables`: changing a limb in
  `PARTS`/`JOINTS` changes every derived dimension.
- `StyleSeam.test_r6_dimensions_are_unchanged`: R6 numbers are pinned.
- `StyleSeam.test_shared_modules_use_only_the_seam`: shared modules import only the seam's
  names from `rig`/`acting`/`faces`/`blender_build`. On its first run this test found one
  leak: `layout.py` sized the hug gap with the R6 head-radius constant instead of the actor's
  body. Fixed: `Body.head_radius`.
- Both films compile to byte-identical plans after these changes (checked against the plans
  their current preview renders were built from).

## The experiment

`python3 benchmarks/experiments/rig_proportions.py VARIANT`: patch `rig.PARTS`/`JOINTS` only,
compile both films, run layout QC (every 2nd frame).

| Variant | Before the `Body` fix | After |
| --- | --- | --- |
| R6 (baseline) | 0 errors | 0 errors |
| legs 2.0 → 2.6 studs | 12 errors: feet under the floor (walk used the old leg length), seated legs into people | 8 errors: seated straight legs reach into staged space (topology + absolute staging) |
| head 1.22 → 0.9 studs tall, 1.3 → 1.0 wide | 0 errors | 0 errors; framing calibrated for the R6 head, unchecked by QC |

Conclusion: proportions can now change through the rig tables alone. A different limb
topology cannot: it touches acting, the plan format, the backend build, QC tolerances and
contact allowances. That is the size of a real second style, and why it should wait until
one is wanted.

## When a second style is wanted

In order, each step keeping both films byte-identical:

1. Put the expression **name** in the plan and resolve it to a face in the style (4).
2. Move framing calibration (`SIZE_STUDS`, `EYE_AT`) to a per-style table keyed by
   head/shoulder size (2).
3. Split `blender_build.py` into the neutral scene build and the character build (6).
4. Let screenplay contact values default from body proportions so a screenplay need not
   carry R6 tuning values (5; also part of the "mechanical defaults" work).
5. Only then: a plan format with a style-declared joint list, and a second `rig`/`acting`
   pair (1, 3).
