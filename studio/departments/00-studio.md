# Blocky Studio — how the studio runs

One agent (or person) can wear every hat, but each hat has its own handbook, its own
command and its own **gate**. A stage is done when its gate passes and the next
department can pick up the files without asking questions.

| Department and roles | Handbook | Command | Gate (blocks the next stage) |
| --- | --- | --- | --- |
| **Production**: producer, production manager, TD | [06-production](06-production.md) | `studio.py new`, `status` | Brief agreed; budget (runtime, locations, cast) fits the machine |
| **Story**: writer, story editor, director | [01-story](01-story.md) | `studio.py story SLUG` | Structure lint passes; director's read-through notes answered |
| **Design**: art director, character, prop and set designers | [02-design](02-design.md) | (data in `screenplay.json`, `kit/sets.py`) | Cast lineup and set occupied-frame look right at phone size |
| **Voice**: casting director, voice actors | [04-sound](04-sound.md#voice) | `studio.py voice SLUG` | Every take intelligible (ASR match >= 0.8) |
| **Layout & animation**: storyboard, previs, layout, rigger, animator | [03-layout-animation](03-layout-animation.md) | `studio.py board`, `studio.py layout SLUG` | Zero collisions, sliding feet, blocked faces or cameras in walls across every frame |
| **Storyboard review**: director | [03-layout-animation](03-layout-animation.md#storyboard-python3-scriptsstudiopy-board-slug---animatic) | `studio.py review SLUG board` | An approving review of the current sheet (a full render refuses to start without it) |
| **Lighting & render**: lighting, FX, render wrangler | [02-design](02-design.md#lighting), [05-post](05-post.md) | `studio.py render SLUG --preset preview\|final` | Frames 1..N, none empty or extra, all rendered from the current plan |
| **Sound**: sound designer, composer, mixer | [04-sound](04-sound.md) | `studio.py sound SLUG --preset ...` | Every line understood through the full mix |
| **Post**: editor, compositor/colourist, captions, delivery QC | [05-post](05-post.md) | `studio.py deliver SLUG --preset ...` | H.264/AAC, 1080x1920 (final), 24 fps, -14 LUFS, decodes to the end, no hard pacing failure |
| **Picture review**: director | [05-post](05-post.md#picture-review-checkpoint) | `studio.py review SLUG picture --preset ...` | An approving review of the current movie, with a disposition for every artistic note |
| **Retrospective**: producer | [06-production](06-production.md#retrospective) | `studio.py retro SLUG` | Verdict answered: made without engine changes? |

`python3 scripts/studio.py make SLUG --preset preview` runs story → voice → layout → board
→ render → sound → deliver. It stops at the first failing gate (exit 2) and at the storyboard
review checkpoint (exit 3) until the review is recorded.

## The order of work

1. **Producer** opens the episode: `studio.py new SLUG --title "..."`. Agree the brief in
   `notes.md`: logline, length (45–65 s), cast (2–4 leads plus extras), locations (3–5 from
   the set library or new ones), delivery (Instagram Reels 1080x1920).
2. **Writer** drafts the screenplay with the [story method](01-story.md). The **story
   editor** runs `studio.py story`. The **director** does a read-through against the
   critique checklist and writes notes in `notes.md`. Iterate until both pass.
3. **Voice** records with `studio.py voice` (offline TTS, ~4x realtime). Real line lengths
   now drive the edit.
4. **Storyboard/layout** run `studio.py board SLUG` (Workbench stills of every shot's key
   moment, labelled with shot id, size, beat and line) and `studio.py layout SLUG`.
   The **director** reviews `renders/SLUG/board/storyboard.jpg`: can you follow the story
   with the sound off? Fix staging in the screenplay, not in Blender. Record the verdict:
   `studio.py review SLUG board --verdict approve|revise --saw "..."`. No full render
   starts without an approving review of the current sheet.
5. **Animation** iterates until layout QC has zero errors. Warnings need a note.
6. **Preview**: `studio.py render SLUG --preset preview` (~0.4–0.5 s/frame), then `sound`,
   then `deliver`. The **director** watches the captioned preview at phone size, twice:
   once muted, once with eyes closed. Notes go to `notes.md` with shot ids and seconds, and
   the verdict is recorded with `studio.py review SLUG picture --preset preview ...`.
7. **Final**: same three commands with `--preset final` (1080x1920, ~1–3 s/frame).
   Publishing is a separate, explicit decision by the creator.
8. **Producer** fills `episodes/SLUG/retrospective.md`, runs `studio.py retro SLUG` and adds
   the film's row to [the generalization ledger](../generalization.md).

## Dailies: how a note is written

`sh16 0:43.1 — Kai's counter is out of frame when it flips to 1. Layout: widen to mcu or
raise the camera. Retest sh16 at its key frame.` One issue per note, with the shot id,
time, what is wrong for the audience, which department owns it and how it will be checked.

## What every department must never do

- Hand-edit the saved `.blend`. Everything is rebuilt from `screenplay.json` and the kit,
  so the next build would silently undo the fix.
- Hide a QC error by deleting it. Either fix the cause or declare the contact on purpose
  (a hug, a hand on a shoulder) through the gesture that creates it.
- Claim a viewing or listening decision nobody made. Agents can verify numbers
  (collisions, loudness, ASR); taste notes say who judged and on what device.

## Files

```
episodes/SLUG/screenplay.json   the single source: cast, voices, lines, beats, scenes, shots, actions, cameras, music cues
episodes/SLUG/voice/            takes/*.wav, timing.json (durations, word times, mouth envelopes), manifest.json
episodes/SLUG/notes.md          brief, director's notes, decisions
episodes/SLUG/reviews.json      recorded storyboard and picture reviews (tied to the exact pictures)
episodes/SLUG/retrospective.md  engine changes, new assets, revisions, failures, interventions, time
renders/SLUG/<preset>/          episode.blend, plan.json, frames/ (+ manifest.json), sound/, captions.ass, SLUG-<preset>.mp4, frames.jpg
renders/SLUG/reports/           story.json, layout.json, listen.*.json, deliver.*.json, pacing.*.json, status.json, history.jsonl
scripts/kit/                    the shared engine (rig, acting, sets, camera, qc, faces, build, voice, sound, captions)
```

The worked example is [One Hug Left](../../episodes/one-hug-left/screenplay.json).
