# Blocky Studio — Roblox-style story shorts

A local animation studio for original, emotional, vertical (9:16) story films in the Roblox
R6 style, made for Instagram Reels. One screenplay file drives every department, and each
department has a handbook, a command and a gate that must pass before the next one starts.
Everything runs offline on one laptop (Blender 5.3, GTX 1650); no stage calls a language model.

| Start here | Open |
| --- | --- |
| Watch the reference episode | [One Hug Left, 1080x1920](renders/one-hug-left/final/one-hug-left-final.mp4) · [without captions](renders/one-hug-left/final/one-hug-left-final-clean.mp4) · [frame per shot](renders/one-hug-left/final/frames.jpg) · [storyboard](renders/one-hug-left/board/storyboard.jpg) |
| Second film (test of the setup) | [Three Words, preview 540x960](renders/three-words/preview/three-words-preview.mp4) · [screenplay](episodes/three-words/screenplay.json) · [notes](episodes/three-words/notes.md) |
| Read how it was made | [Screenplay](episodes/one-hug-left/screenplay.json) · [Brief and director's notes](episodes/one-hug-left/notes.md) |
| Agents start here | [Agent docs](Agent_Docs/README.md): [tools guide](Agent_Docs/00-tools-guide.md) · [agent guide](Agent_Docs/04-agent-guide.md) · [production](Agent_Docs/03-production.md) · [Blender](Agent_Docs/01-blender.md) · [sound](Agent_Docs/02-sound.md) |
| Learn the studio | [Studio handbook](studio/departments/00-studio.md), then the departments: [story](studio/departments/01-story.md) · [design](studio/departments/02-design.md) · [layout & animation](studio/departments/03-layout-animation.md) · [voice & sound](studio/departments/04-sound.md) · [post & delivery](studio/departments/05-post.md) · [production & engine](studio/departments/06-production.md) |

## Make an episode

```sh
python3 scripts/studio.py new my-story --title "My Story"   # producer: scaffold the episode
# write episodes/my-story/screenplay.json with the story handbook, then:
python3 scripts/studio.py story   my-story                 # story gate
python3 scripts/studio.py voice   my-story                 # offline TTS + word timing
python3 scripts/studio.py board   my-story                 # storyboard sheet (seconds)
python3 scripts/studio.py layout  my-story                 # animation QC on every frame
python3 scripts/studio.py review  my-story board --verdict approve --saw "..."  # look at the sheet first
python3 scripts/studio.py make    my-story --preset preview  # render, sound, deliver (~15 min)
python3 scripts/studio.py make    my-story --preset final    # 1080x1920 (~75 min per minute of film)
python3 scripts/studio.py review  my-story picture --preset preview --verdict ... --saw "..."
python3 scripts/studio.py status  my-story                 # what ran, what is stale and why
python3 scripts/studio.py retro   my-story                 # measured part of the retrospective
```

`make` runs story → voice → layout → board → render → sound → deliver and stops at the
first failed gate, and at the storyboard review: a person or agent looks at the sheet and
records a review before anything is rendered. After delivery the movie gets a picture
review, and each film gets a short retrospective
([gates, reviews and retrospectives](Agent_Docs/05-gates-reviews-retros.md)). The finished file lands in `renders/SLUG/<preset>/SLUG-<preset>.mp4`, with a
clean copy without captions and a contact sheet beside it.

Rebuild the reference episode: `python3 scripts/studio.py make one-hug-left --preset final`
(PNG frames are not kept in the repo, so this re-renders everything).

## How it works

```
screenplay.json ─► story gate ─► voice takes + word times ─► layout plan (every frame posed)
                                                                 │
                    collision / feet / camera / continuity QC ◄──┘  (no render until 0 errors)
                                                                 │
             Blender build (characters, faces, sets, lights, cameras, counters, FX)
                                                                 │
             render ─► foley from animation events + score + mix ─► captions ─► delivery QC
```

- **`scripts/studio.py`** — the command line for every department and its gates.
- **`scripts/kit/`** — the shared engine: R6 rig and acting (planted-foot walks, sits,
  gestures aimed at real targets, 25 expressions, lip-sync), the set library as data,
  a 9:16 camera solver, per-frame QC, the story gate, the Blender builder, voice,
  sound and captions. The module map is in [06-production](studio/departments/06-production.md#the-engine).
- **`scripts/video_audit.py`** — pacing audit (cuts, motion, frozen stretches) against
  [`studio/quality-bar.json`](studio/quality-bar.json), measured from the reference reels.
  Defects block delivery; matters of taste go to the picture review.
- **`tests/`** — no Blender needed: `python3 tests/test_kit.py` (engine, both films as
  fixtures) and `python3 tests/test_studio.py` (pipeline gates).

## Setup

| Tool | Used for | Where |
| --- | --- | --- |
| Blender 5.3 (`blender` on PATH) | build, render | system |
| FFmpeg with libass, rubberband | encode, captions, pitch/tempo of voices | system |
| Fira Sans font | captions | system (`fc-list \| grep "Fira Sans"`) |
| Python 3 (host) | `studio.py`, the kit, tests (standard library only) | system |
| `.audio-tools/venv` (numpy, kokoro-onnx, onnxruntime) | voice and sound | local, git-ignored |
| `.audio-tools/asr` (faster-whisper, model `small.en` cached) | word timing, listening gate | local, git-ignored |
| `.audio-tools/bin/crispasr` + `models/qwen3-tts-*.gguf` | Qwen3-TTS voices | local, git-ignored |
| `.audio-tools/models/kokoro-v1.0.int8.onnx`, `voices-v1.0.bin` | Kokoro voices | local, git-ignored |

`studio.py` launches Blender with a clean environment, so a pyenv Python on PATH cannot
break Blender's own Python.

## Repository

```
AGENTS.md            instructions for coding agents (Codex): work as the whole studio
Agent_Docs/          full references: agent guide, production, Blender, sound (enough to rebuild the studio)
studio/departments/  the handbooks, one per department
studio/templates/    screenplay and retrospective templates used by `studio.py new`
studio/*.json        quality bar (length, pacing) and story rubric (critic pass)
studio/generalization.md  one row per film: engine changes needed (the number should fall)
episodes/SLUG/       screenplay.json, notes.md, reviews.json, retrospective.md, voice/ (takes and timing)
renders/SLUG/        board/, preview/, final/ (blend, plan, sound, mp4s), reports/
guides/              reference reels the format was studied from (videos are git-ignored)
archive/             old films from before the studio, for comparison only (git-ignored)
```

Posting to Instagram is always the creator's own decision.
