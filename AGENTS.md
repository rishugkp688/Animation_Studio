# Blocky Studio — Roblox-style story shorts

We make short, original, emotional vertical films (45–65 s target, set in `studio/quality-bar.json`; 1080x1920, 24 fps) in the
Roblox R6 style for Instagram Reels, on Linux with Blender's Python API. The creator uses
local Codex in VS Code on a GTX 1650 (4 GB). Everything runs locally; no stage calls a
language model, so re-rendering costs nothing.

## How to work: you are the whole studio

New here? Read [Agent_Docs/04-agent-guide.md](Agent_Docs/04-agent-guide.md) first: it explains how the
engine works, the mistakes already made, and how to behave. [Agent_Docs/](Agent_Docs/README.md) also has the
full Blender, sound and production references, and [a plain-language tools guide](Agent_Docs/00-tools-guide.md).

Then read [the studio handbook](studio/departments/00-studio.md). It maps every role
(producer, writer, director, designers, layout, animator, voice, sound, editor, delivery)
to a handbook, a command and a gate. Work through the stages in order and do not skip a
gate. Wear one hat at a time and write the director's notes in `episodes/SLUG/notes.md`.

```sh
python3 scripts/studio.py new SLUG --title "Title"     # producer
python3 scripts/studio.py story SLUG                   # writer + story editor gate
python3 scripts/studio.py voice SLUG                   # casting and recording (offline TTS)
python3 scripts/studio.py board SLUG                   # storyboard sheet (Workbench, seconds)
python3 scripts/studio.py layout SLUG                  # layout + animation QC on every frame
python3 scripts/studio.py review SLUG board --verdict approve|revise --saw "..."   # CHECKPOINT: look at the sheet
python3 scripts/studio.py render SLUG --preset preview # ~0.4–0.5 s/frame
python3 scripts/studio.py sound SLUG --preset preview  # ambience, foley, score, mix + listening gate
python3 scripts/studio.py deliver SLUG --preset preview
python3 scripts/studio.py review SLUG picture --preset preview --verdict ... --saw "..." --note CODE="..."  # CHECKPOINT
python3 scripts/studio.py make SLUG --preset final     # everything; stops at a failed gate or the board review
python3 scripts/studio.py status SLUG                  # what is stale and why, reviews, DONE
python3 scripts/studio.py retro SLUG                   # producer: measured half of the retrospective
```

The two review checkpoints are judgement, kept outside the engine: you look (storyboard;
movie at phone size and `frames.jpg`), then record what you saw. The engine only checks that
the review exists, approves, says enough and matches the current pictures. Hard pacing
defects block delivery; artistic notes (cutting rate, stillness, length) need a written
disposition in the picture review. A shot may freeze on purpose with `"hold": "why"`. See
[gates, reviews and retrospectives](Agent_Docs/05-gates-reviews-retros.md).

When asked for "a story", write a real one: follow the [story method](studio/departments/01-story.md)
(world rule in the first line, the hero as the exception, a person who seems wrong, a turn, a
reveal, a costly choice, a button line that echoes a plant). Three or more locations, 20–28
shots, six or more expressions on the lead, dialogue used sparingly. Then read it as the
director against the critique checklist before building anything.

## The engine (`scripts/kit/`)

One shared engine replaces per-episode builders. The screenplay (`episodes/SLUG/screenplay.json`)
is the single source of truth: cast and looks, voices, lines, beats, setups, music cues,
scenes, shots, actions and cameras. [Worked example: One Hug Left](episodes/one-hug-left/screenplay.json).

- `rig.py` true R6 proportions and forward kinematics; `acting.py` planted-foot walking,
  sitting, gestures that aim at real targets, eyes-lead-head looks, blinks, lip-sync, idle life.
- `sets.py` the location library as data (geometry, marks, seats, lights, times of day).
- `camera.py` shot sizes for 9:16, angle search around blocked faces, wild-wall hiding.
- `qc.py` per-frame collision, foot, framing and continuity checks; `story.py` the story gate.
- `blender_build.py` builds characters, faces, sets, lights, cameras, counters and FX in Blender.
- `voice.py`/`align.py` offline TTS (Qwen3-TTS, Kokoro) with word timing; `sound.py` ambience,
  foley from animation events, procedural score, −14 LUFS master; `captions.py` word-timed captions.

Rules: fix problems in the screenplay or the kit, never in a saved `.blend`. Pure-Python
modules must not import `bpy`. Add a test to `tests/test_kit.py` for new kit behaviour and to
`tests/test_studio.py` for pipeline behaviour; run both. Every finished film joins `FILMS` in
`tests/test_kit.py`. Find fonts with `kit/fonts.py`, never a hard-coded path.
Run Blender scripts headless (`blender -b --factory-startup -P ...`). Use the
`.audio-tools/venv` Python for voice and sound, `.audio-tools/asr` for listening checks.

## What "done" means

All gates pass: story, voice intelligibility, zero layout errors, all frames rendered from the
current plan, every line understood through the mix, and delivery QC on the actual file
(H.264/AAC, 1080x1920, 24 fps, −14 LUFS, true peak ≤ −1 dBTP, decodes to the end, no hard pacing
failure). Both review checkpoints approve the current pictures (`status` prints `DONE` for the
final). Then fill `episodes/SLUG/retrospective.md` (was the film made without changing the
engine?), run `studio.py retro SLUG`, and add the film's row to `studio/generalization.md`.
The current goal is to show the engine generalises across very different films, not to grow
the architecture: no new agents, model calls, render backends or styles. Report the movie path and the rebuild command. If something
could not be verified (for example, listening by ear), say so plainly. Posting to Instagram
needs the creator's explicit go-ahead.

## Repository

Only the studio lives here: `scripts/studio.py`, `scripts/kit/`, `scripts/video_audit.py`,
`studio/`, `tests/`, and `episodes/` + `renders/` per film. The reference reels
are in `guides/` (videos git-ignored). `archive/legacy-films/` holds cuts of films made before
the studio, for comparison only; their builders were removed and must not be revived.
