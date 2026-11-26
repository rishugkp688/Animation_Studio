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
**stale**, naming the cause, when anything it was built from changed: the screenplay (voice
only looks at lines, voices and narrator), the engine code that stage runs (its kit modules
in `studio.KIT_FILES`, `studio.py`, `video_audit.py`), configuration (`studio/quality-bar.json`), assets (fonts, voice takes),
tools (Blender, FFmpeg, the audio venvs' packages) or an upstream artifact (timing, plan,
frame manifest, mix, storyboard, movie). Staleness flows downstream. It also shows both
reviews, the retrospective, and `DONE`. Reports live in `renders/SLUG/reports/`
(`history.jsonl` logs every stage run). Notes and decisions live in `episodes/SLUG/notes.md`.

## Retrospective

Every film gets `episodes/SLUG/retrospective.md` (created by `new`, template in
`studio/templates/`): the verdict (made without modifying the core engine: yes/no), engine
modifications and why, new reusable assets and behaviours, screenplay revisions, QC
failures, manual interventions, production and rendering time. `studio.py retro SLUG`
refreshes its measured section from the history, the reviews and git. When the film is
done, add its row to [the generalization ledger](../generalization.md). The number to watch
is engine changes per film: it should fall until a film needs only a screenplay.

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
| `fonts.py` | finds the house font on any distro (overrides `BLOCKY_FONT_*`) | anywhere |

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
Add a test in `tests/test_kit.py` for any new engine behaviour (and in `tests/test_studio.py`
for pipeline behaviour) and run `python3 tests/test_kit.py && python3 tests/test_studio.py`.
Every finished film is added to `FILMS` in `test_kit.py` so it stays a regression fixture.
Find fonts through `kit/fonts.py`. A new kit module goes into `studio.KIT_FILES` under every
stage that runs it. Never fix a defect only in a saved `.blend`. Record each
engine change in the film's retrospective with the reason the screenplay could not express it.

## Archive

Films made before this studio (Paper Cranes, Me Too, Still Warm, The Nine O'Clock Light, ...)
were built by one-off scripts that were removed on 2026-11-26. Their last cuts are kept in
`archive/legacy-films/` (git-ignored) only for comparison; the few old builders that were committed
remain in git history. Every new film goes through this studio.
