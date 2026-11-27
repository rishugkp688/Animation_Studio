# Operator card

The one page you need to **run** the studio. To change the engine, read
[AGENTS.md](AGENTS.md) and [Agent_Docs/04-agent-guide.md](Agent_Docs/04-agent-guide.md) instead.

## The loop

Every command is `python3 scripts/studio.py COMMAND SLUG`. Exit code 0 = pass, 2 = a gate
failed (read its output), 3 = waiting for a review. Run them in this order; never skip one.

| # | Command | Passes when | When it fails, read |
| --- | --- | --- | --- |
| 1 | `new SLUG --title "..."` | creates `episodes/SLUG/` | — |
| 2 | `story` | structure, length, vocabulary are valid | its FAIL lines |
| 3 | `voice` | every line was recorded and understood | `unclear: [ids]` → respell with `"say"` on that line |
| 4 | `layout` | 0 errors on every frame | its `erro` lines; `renders/SLUG/reports/shots.txt` |
| 5 | `board` | the storyboard sheet is built | — |
| 6 | `review SLUG board --verdict approve\|revise --saw "..."` | you approve; **needs layout to pass first** | — |
| 7 | `render --preset preview` · `sound` · `deliver` | all frames, mix and file QC pass | their FAIL lines |
| 8 | `review SLUG picture --preset preview ...` | you approve, with `--note CODE="..."` per artistic note | — |
| — | `status SLUG` | shows what is stale and why | — |

After **any** screenplay edit, run `layout` again before `board`: a sheet built from
staging that has not passed QC is marked with a WARNING and cannot be approved.

## Layout errors: what they mean and what to change

| Message | Cause | Change (in `screenplay.json`) |
| --- | --- | --- |
| `leo.torso inside 'bedside'` (collision.set) | the actor stands or walks to a spot inside furniture | the walk's `to`, or the scene `cast` start `at`, by 0.2-0.6 m away from it |
| `leo.torso inside ava.leg_r` (collision.actor) | two actors overlap | the walk's `to` (stop further away), an approach's `gap`, or a hug's `at`/`lift` |
| `feet.slide`, `feet.sink` | a planted foot moves or sinks | an actor jumps to a new place mid-shot: move them with a `walk` instead (walking plants feet itself) |
| `camera.solve: X blocks ava's face` | something is between the lens and the face in a close shot | `cam.side`, `cam.height`, `cam.size`, or move the blocker |
| `face out of frame` | the subject leaves the frame | `cam.size` wider, `cam.side`, or `cam.on` |

Fix errors by hand first: read the message, change the field, rerun `layout`. Most faults
take one edit each. If you are stuck, the studio can search for small mechanical fixes:
`studio.py repair SLUG` (proposes, changes nothing; **takes up to 4 minutes**, so give your
shell command at least 5) → `repair SLUG --apply` → `layout SLUG`. It only moves positions,
approach gaps and hug contact, fixes independent faults but not ones that depend on each
other, and can be undone with `repair SLUG --undo`.

## Screenplay quick reference

- Coordinates `[x, y]` are metres in the scene's set. A set's named marks and seats can be used
  instead of coordinates (list them with `python3 -c "import sys; sys.path.insert(0,'scripts'); from kit import sets; s=sets.load('SET'); print(s.marks, list(s.seats))"`).
- `cam.size`: `ecu cu mcu ms fs ws ews two ots insert`. `cam.on`: an actor, or two for `two`/`ots`.
- `cam.side`: degrees around the subject from the front of their face, + = toward the subject's left.
  For a `two`, 0 = square-on to the pair, on the side they face. Values near ±180 show the back of a head.
- `cam.height`: `eye low high top worm slight_high slight_low`. `cam.move`: `push pull drift static arc track rise push_fast`.
- Actions and expressions must be known names; the story gate lists the valid ones when one is not.
  `studio.py catalog [sets|actions|expressions|camera|sound|looks|voices]` prints everything a
  screenplay can use (with parameters as used in the finished films); `studio.py schema SLUG`
  checks a screenplay's structure.
- Hugs: give `with` and `dur`; contact point, lift and head turn default by the actors' heights.

## Reviews

Look at `renders/SLUG/board/storyboard.jpg` (and the movie and `frames.jpg` for the picture
review). **If you cannot view images**, read `renders/SLUG/reports/shots.txt`, which lists every
shot's framing, who is visible and facing where, and flags empty frames, hidden faces and
backs of heads. Review from it, and say so in `--saw` and `--reviewer`. It measures the plan,
not the picture: it cannot judge lighting, composition or emotion.

## Never

- Edit `scripts/`, `studio/`, docs, tests, other episodes, `reports/status.json` or any
  `.blend`. Fix things in `episodes/SLUG/screenplay.json`.
- Pass `--unreviewed` or `layout --quick` off as the gate.
- Claim you looked at, or listened to, something you did not.
- Post anything anywhere.
