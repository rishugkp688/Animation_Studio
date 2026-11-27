# Gates, staleness, reviews and retrospectives

What changed in November 2026, how to use it, what the tests prove, and what is still not
covered. The changes came from an outside review of the repository, which was checked against
the code before anything was changed (six of its seven findings were real; the seventh, "the
layout test skips foot sliding", was half right; see *Fixes* below).

The direction stays the same: **no new agents, model calls, render backends, styles or
per-shot caching.** The next goal is to show the engine generalises across very different
films, measured by the [retrospective](#retrospective-one-per-film) of each one.

## The pipeline now

```
story → voice → layout → board ─▶ REVIEW (board) ─▶ render → sound → deliver ─▶ REVIEW (picture)
                                     a person or agent looks; the engine records and checks
```

`studio.py make SLUG` runs every stage in order. It re-renders the board only when the board's
inputs changed, so a review stays valid. It stops with exit code 3 at the board checkpoint
until an approving review matches the current sheet, and after delivery it prints the
command for the picture review. Exit codes: 2 = a gate failed, 3 = waiting for a review.

## Fixes

| # | Problem (verified) | What the studio does now |
| --- | --- | --- |
| 1 | The pacing audit ran but never blocked delivery. | Pacing results are split into **hard** failures (defects: frozen picture, single-frame flashes, opening on black, audio peak), which block `deliver`, and **artistic** notes (cuts per minute, median/longest shot, static shots, mean motion, first-second motion, duration, loudness), which never block delivery but each need a written disposition in the picture review. **Intentional stillness:** a shot with `"hold": "why it is held"` in the screenplay excuses frozen picture inside that shot. A freeze that runs past the shot is still a failure. The story gate rejects a `hold` that does not say why. |
| 2 | Frame checks only counted files. | Every render writes `frames/manifest.json` recording which plan each frame was rendered from, with its size and mtime. `render` and `deliver` require exactly frames 1..N, none extra, none empty, none changed after rendering, all from the current `plan.json`. A full render keeps only frames already rendered from the plan it is about to build (so an interrupted render still resumes) and deletes stale, empty (Blender's placeholder for an interrupted frame) and unrecorded frames. `--frames A-B` deletes that range first so it is really re-rendered. |
| 3 | "Stale" only looked at the screenplay and the kit. | Each stage records the fingerprints of everything it depends on: **engine** (only the kit modules that stage runs, listed in `studio.KIT_FILES`: editing the story gate does not make a render stale, editing the camera solver does; `studio.py` for the stages whose logic lives there; `video_audit.py` for delivery), **configuration** (`studio/quality-bar.json`), **assets** (fonts, recorded voice takes), **tools** (Blender and FFmpeg binaries, installed packages of the two audio venvs) and **upstream artifacts** (`voice/timing.json`, `plan.json`, the frame manifest, `mix.wav`, the storyboard sheet, the movie). `status` names what changed, and staleness propagates to every downstream stage. Voice only depends on the lines, voices and narrator, so changing a camera does not ask for new takes. Python caches are ignored. `deliver` refuses a mix whose sound gate failed or whose inputs (plan, takes, screenplay) changed since. |
| 4 | Episode tests used one film; the foot-slide filter did nothing. | The episode tests run on **both** films (One Hug Left and Three Words). The `feet.slide` exclusion was removed: at `step=3` that rule never fires, because it needs consecutive frames. Foot sliding is covered by `test_walk_keeps_stance_feet_planted` and by the layout gate, which checks every frame. `layout --quick` is now informational only: it is not recorded as the gate, because it skips the foot-slide check. |
| 5 | `make` skipped the storyboard. | `make` builds the board when it is not current and stops at the board review checkpoint. A full `render` also refuses to start without a current, approved board review, unless `--unreviewed "why"` is passed, which is logged as a manual intervention. |
| 6 | Font paths were hard-coded to Arch's `/usr/share/fonts/TTF`. | `kit/fonts.py` finds fonts: first `BLOCKY_FONT_BOLD` / `_REGULAR` / `_EXTRABOLD` (and `BLOCKY_FONT_FAMILY`), then Fira Sans, Noto Sans or DejaVu Sans in the usual Arch, Debian/Ubuntu and Fedora folders, then `fc-match`. The storyboard sheet, 3D counters/props and burned-in captions all use it, and captions name the family that was found. |
| 7 | Three places disagreed on film length (docs 45–65 s, story gate 30–78 s, pacing bar 30–75 s). | `studio/quality-bar.json` → `duration_s` is the one source: **target 45–65 s** (outside it the story gate and pacing audit warn, and the picture review must justify the length), **limit 30–78 s** (outside it the story gate fails). |

## Visual review checkpoints

The engine never judges whether a picture is good. A person, or the agent acting as director,
looks and writes down what they saw. The engine records it in `episodes/SLUG/reviews.json`
(committed with the film) with the fingerprints of the exact pictures reviewed, and treats
the review as void as soon as those pictures change.

```sh
# 1. Storyboard: before any full render
python3 scripts/studio.py review SLUG board --verdict approve|revise \
    --saw "sh01-sh04 read muted; sh09 turn lands; sh14 two-shot is cramped ..." [--reviewer NAME]

# 2. Picture: after deliver, for that preset
python3 scripts/studio.py review SLUG picture --preset preview --verdict approve|revise \
    --saw "what you saw, with shot ids and seconds" \
    --note first_second_motion="why it is right, or what will be done" --note duration="..."
```

- What to look at: the board review covers `renders/SLUG/board/storyboard.jpg` (and the
  animatic if one was made). The picture review covers the captioned movie at phone size,
  `frames.jpg`, and single frames at the hook, the reveal and the button. Use the critique
  checklists in [the story handbook](../studio/departments/01-story.md) and
  [the post handbook](../studio/departments/05-post.md).
- `--saw` needs at least 12 words. Name shots, seconds, what reads and what does not.
- Approving the picture needs a `--note CODE="..."` (3+ words) for every artistic note that
  `deliver` printed. A `revise` verdict is recorded but does not approve anything.
- Say who reviewed and how. An agent that only looked at stills and did not listen says so in
  `--reviewer` and in `--saw`.
- A film is **done** (`status` prints `DONE`) when `deliver.final` passed, all its inputs
  are current and the final picture review approves the current movie.

## Retrospective: one per film

`studio.py new` writes `episodes/SLUG/retrospective.md` from
[the template](../studio/templates/retrospective.md). The producer fills in the human part:

- **Verdict:** "Produced without modifying the core engine: yes/no", and how many engine
  changes were needed.
- **Engine modifications and why:** file, change, and why the screenplay could not express it.
- **New reusable assets and behaviours.**
- **Screenplay revisions** and which gate or review asked for them.
- **Quality-control failures** that taught something.
- **Manual interventions:** overrides, hand edits, selective re-renders, re-recorded takes.
- **Production and rendering time.**

`studio.py retro SLUG` refreshes the **Measured** section (between the
`<!-- measured:start/end -->` markers, everything else is kept) from
`renders/SLUG/reports/history.jsonl`, the reviews and git. It covers runs, passes and
failures per stage with total time, render s/frame, every gate failure with its details,
how many screenplay and engine versions the gates saw, recorded overrides, engine commits
since production started, and uncommitted engine changes. `status` shows whether the
retrospective exists and whether the verdict is answered. Every stage run appends to
`history.jsonl`, so the history starts with the first run after this change.

The cross-film ledger is [studio/generalization.md](../studio/generalization.md).

## Tests

```sh
python3 tests/test_kit.py      # engine, both films as fixtures (~2.5 min)
python3 tests/test_studio.py   # pipeline, 46 tests (~20 s; the delivery tests need ffmpeg)
```

`test_studio.py` builds a throwaway episode and proves each fix in both directions,
including negative cases where an invalid artifact must stop delivery:

- **Frames:** complete render passes; missing, extra, empty, edited and older-plan frames
  block; legacy frames are adopted only when all are newer than the build; resume keeps only
  current-plan frames; a partial render removes its range; the predicted plan matches the
  plan Blender actually wrote for Three Words byte for byte.
- **Pacing:** each hard code and each artistic code lands in the right list, and the codes
  match `quality-bar.json`. A real 4 s FFmpeg clip that freezes for 2.5 s is caught by the
  audit and excused by a hold. End to end, `cmd_deliver` on a synthetic film **refuses**
  delivery for the freeze, **passes** the same film once the shot declares a `hold`, and
  refuses a missing frame (before encoding), a mix made for another plan, and a failed sound
  gate.
- **Staleness:** voice timing, engine code (not caches; only the modules a stage runs, and every kit module must be assigned to a stage), quality bar, voice takes and line
  text each make the right stage stale. A camera change does not make voice stale. Staleness
  propagates downstream, and pre-change stamps count as stale.
- **Reviews and make:** no review blocks a full render (exit 3); a one-line review is
  refused; approval is void once the sheet changes; `revise` never approves; picture approval
  needs a disposition per artistic note and a passed delivery; `make` builds the board and
  stops for review, and keeps a current board and runs to delivery once approved.
- **Retrospective:** `new` creates it; `retro` measures failures, interventions and
  per-stage counts, keeps the written part and reads the verdict.

`test_kit.py` adds: the duration target and limit come from the quality bar; a `hold` must
say why; font fallback order and override; captions use the font found; and five checks per
film (story gate, layout errors at every 3rd frame, close-up eye line, narrator, duration
limit) for both films.

## Backward compatibility

- **Old stamps** in `renders/*/reports/status.json` have no input record, so `status` shows
  them as stale until the stage runs again. Nothing is deleted.
- **Old frames** with no manifest are adopted on the first `render`/`deliver` only if every
  frame is newer than `plan.json` (one complete render after the last build). Otherwise they
  are reported as unrecorded and must be re-rendered.
- **A full render needs an approved board review** (or `--unreviewed "why"`). For an
  existing film: `board`, look at the sheet, then `review SLUG board ...`.
- **`deliver` needs a current `sound` stamp:** run `sound` again once after this change (it
  takes under a minute).
- **`layout --quick`** no longer counts as the layout gate.
- **`video_audit.py`:** `judge()` now returns `(hard, artistic, excused)` as lists of
  `{code, msg}` instead of `(fails, warns)` strings; its JSON uses `hard`/`artistic`/`excused`
  in place of `fails`/`warnings`; `--strict` exits non-zero only on hard failures. New
  `--plan`/`--screenplay` flags apply holds.
- **`quality-bar.json`** is version 2: `video.min_seconds`/`max_seconds` moved to
  `duration_s`; new `pacing.hard`/`pacing.artistic` lists.
- **`captions.build()`** takes an optional `font=` (default "Fira Sans"); existing calls work.
- **Both films still pass** every gate; see each film's `retrospective.md` and `notes.md`.

## Remaining limitations

- Reviews are recorded, not verified: the engine can prove that a review names the current
  pictures and says enough, not that anyone looked carefully. That is deliberate (judgement
  stays outside the engine), and the reviewer field must say who looked and how.
- The kit-module lists per stage (`studio.KIT_FILES`) were made by following imports by hand.
  A new import between kit modules must be added there; a test fails if a module is listed
  nowhere, but not if it is listed under the wrong stage.
- Tool fingerprints use the binary's path, size and mtime, and the venvs' package lists. TTS
  and ASR model weights inside `.audio-tools` are not hashed (they are gigabytes); an
  in-place model swap would not mark voice stale.
- The frame manifest trusts size and mtime, not pixel content. A file rewritten with the same
  size and a restored mtime would pass (no studio command does that).
- Any change to the plan invalidates every frame. Selective re-rendering of unchanged shots
  needs per-shot plan fingerprints, which is the per-shot caching deliberately left out for now.
- The resume logic predicts `plan.json` by compiling the plan in the studio's Python. It
  matched Blender's output byte for byte on this machine (Blender 5.3, Python 3.14). If it ever
  differs, the kept frames are flagged as older-plan frames and the next full render removes
  them, so the failure is safe but costs a render.
- The storyboard sheet is not byte-identical between rebuilds, so any rebuild (including one
  caused only by an engine change) voids the board review and needs a fresh look. `make`
  only rebuilds the board when its inputs changed, so an unchanged film keeps its review.
- `make` still runs story, voice and layout every time. Voice reuses takes; layout takes about
  35 s.
- Story-gate thresholds other than duration (shots per minute 18, words per minute 165 vs
  the bar's 150) are still in `kit/story.py`; only length was unified.
- Visual review is the only check of artistic quality. The pacing audit measures motion and
  cutting, not whether a shot is well composed or an emotion lands.
