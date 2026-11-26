# Production — producer, production manager, technical director

## Producer: scope that fits the machine

The studio laptop is a GTX 1650 (4 GB). Budget per 60 s episode, measured on One Hug Left:

| Stage | Time |
| --- | --- |
| Voice (18 lines, Qwen3-TTS on CPU) | ~4 min |
| Layout QC (1,500 frames, full rate) | ~1 min |
| Build | ~25 s |
| Preview render (540x960) | ~10–13 min |
| Sound (ambience, foley, score, mix) | ~30 s |
| Final render (1080x1920) | measure one frame with `--stills`, then multiply |

Keep a 60 s episode to 3–5 locations, 2–4 leads plus a few extras, 20–28 shots. Each new
set costs a design pass; reuse the library first.

## Production manager: tracking

`python3 scripts/studio.py status SLUG` shows each stage's last result and marks a stage
**stale** when the screenplay or the kit changed after it ran. Reports live in
`renders/SLUG/reports/`. Notes and decisions live in `episodes/SLUG/notes.md`.

## The engine

| Module | Job | Runs in |
| --- | --- | --- |
| `mathx.py` | vectors, matrices (Blender XYZ euler), easing, noise | anywhere |
| `rig.py` | true R6 body (1 stud = 0.33 m), forward kinematics, part boxes, key points | anywhere |
| `acting.py` | `Performer`: walk (planted feet), turn, sit, stand, crouch, gestures, looks, talk, blinks, expressions, counters | anywhere |
| `sets.py` | the set library as data: geometry, marks, seats, lights, time of day | anywhere |
| `geom.py` | oriented boxes, overlap depth, ray tests, navigation (A*) around furniture | anywhere |
| `camera.py` | 9:16 shot sizes in studs, angle search around blocked faces, moves, wild walls | anywhere |
| `layout.py` | compiles the screenplay into a timed plan: placements, actions, shots, music cues | anywhere |
| `qc.py` | per-frame collision, foot, framing and cut checks | anywhere |
| `story.py` | the story gate | anywhere |
| `faces.py` | draws the eye/mouth/extras atlases and face details | Blender's Python (PIL) |
| `blender_build.py` | builds characters, sets, lights, cameras, counters, FX; renders | Blender |
| `voice.py` | Qwen3-TTS / Kokoro takes, pitch and tempo, levels, mouth envelopes | `.audio-tools/venv` |
| `align.py`, `listen.py` | word timing; intelligibility through the mix | `.audio-tools/asr` |
| `sound.py` | ambience, foley from animation events, score, ducking, −14 LUFS master | `.audio-tools/venv` |
| `captions.py` | word-timed ASS captions | anywhere |

## Technical director: extending the kit

| Need | Where |
| --- | --- |
| A new gesture | `GESTURES` in `scripts/kit/acting.py`: a function returning arm/neck targets, optional lean, and the contacts it intends |
| A new expression | `EXPRESSIONS` in `acting.py` (combine existing eyes/mouths) or draw a new cell in `faces.py` |
| A new hair style or outfit | `build_hair` / `build_outfit` in `scripts/kit/blender_build.py` |
| A new set | A function in `scripts/kit/sets.py` added to `LIBRARY` (see [02-design](02-design.md)) |
| A new ambience or foley sound | `ambience()` / event handling in `scripts/kit/sound.py` |
| A new camera size or move | `scripts/kit/camera.py` |
| A new QC rule | `scripts/kit/qc.py` (pure Python, runs without Blender) |

Rules: the pure-Python modules (`mathx`, `rig`, `acting`, `sets`, `geom`, `camera`,
`layout`, `qc`, `story`) must not import `bpy`; they run in tests and in Blender alike.
Add a test in `tests/test_kit.py` for any new behaviour and run `python3 -m unittest
tests.test_kit`. Never fix a defect only in a saved `.blend`.

## Archive

Films made before this studio (Paper Cranes, Me Too, Still Warm, The Nine O'Clock Light, ...)
were built by one-off scripts that were removed on 2026-11-26. Their last cuts are kept in
`archive/legacy-films/` (git-ignored) only for comparison; the few old builders that were committed
remain in git history. Every new film goes through this studio.
