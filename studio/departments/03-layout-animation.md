# Layout and animation — storyboard, previs, layout, rigger, animator

Animation is **written in the screenplay** as actions with times, and **solved and verified
by the kit**. Nobody keys a pose by hand, so every episode gets the same planted feet,
contact rules and camera logic. The animator's job is choosing actions, timing and acting
beats, then reading the QC report and fixing the cause.

## How motion is produced (`scripts/kit/acting.py`)

- **Walking/running** plants each foot at a world position and aims the rigid R6 leg at
  it; the hip height comes from the leg length (an inverted pendulum). Stance feet cannot
  slide. Routes go around furniture with A* on the set's real geometry.
- **Sitting / standing** travel first, then lower or rise, so legs never sweep through a
  seat or a bed edge. **Turning** in place takes two small steps.
- **Gestures** aim limbs at real targets (a partner's back, a head, a shoulder) every
  frame, so contacts stay on as people move.
- **Eyes lead the head**: on a new look target the pupils jump first, the head follows in
  0.38 s. Blinks happen every 2.6–4.5 s, on expression changes and on big head turns.
- **Idle life**: breathing, a slow weight shift and small head drift. Nobody freezes.
- **Lip-sync** for on-screen dialogue comes from the take's loudness envelope.

## Action vocabulary (inside a shot's `act` list)

Timing: `"t": 1.2` (seconds into the shot), `"t": "line:n04+0.3"`, `"t": "end:g01-0.2"`,
`"t": "+0.3"` (after this actor's previous action). Any action may carry `"expr"` and `"look"`.

| Action | Parameters | Notes |
| --- | --- | --- |
| `walk`, `run` | `to`: mark, seat, actor or `[x, y]`; `face`, `face_offset`, `speed`, `dur`, `backward`, `gap` (from an actor) | Route avoids furniture. Use `dur` to fit a beat. |
| `approach` | `to`: actor; `side` (0 = in front of them), `run`, `offset` | Stops at embrace distance computed from both bodies. |
| `turn` | `to`: actor, mark, point or yaw degrees; `dur` | |
| `sit` / `stand` | `seat` / `to` | Walks to the seat first if needed. |
| `look` | `at`: actor, point name, `[x, y, z]`, `cam`, `none` | Eyes lead, head follows. |
| `face` | `expr` | See `EXPRESSIONS`: neutral, calm, happy, joy, tender, sad, hurt, teary, cry, sob, cry_smile, teary_smile, shock, surprise, worried, scared, angry, grumpy, determined, sleepy, smirk, shy, hopeful, eyes_closed, peaceful |
| `hug` | `with`, `at` (`hips` for the shorter actor, `head` for the taller), `lift`, `lean`, `head_turn`, `head_pitch`, `dur` | Taller actor holds high, shorter low; arms never cross the partner. |
| `hold_shoulders` | `with`, `lift`, `head_pitch` | Arm's-length moment after a hug. |
| `release` | `gesture` (optional) | Ends a long gesture now. Release the child first, step back, then the adult. |
| `open_arms` | `spread`, `raise` (≥ 1.4 when someone runs in) | Open before closing a hug. |
| `ruffle`, `hold_hand`, `point`, `reach`, `offer`, `wave` | `with`/`at`/`to`, `hand` | |
| `hands_face`, `sob`, `head_down`, `nod`, `shake_head`, `shrug`, `flinch`, `hand_chest`, `think`, `look_around`, `lean_forward`, `hands_behind`, `blocked_hug`, `crouch` | `dur`, `amount` | |
| `badge` | `text`, `sfx` | Changes an overhead counter with a pop, sparkle and chime. |
| `fx` | `kind`: `ripple` (invisible wall), `at` | |

## Camera vocabulary (`cam` on each shot)

`size`: `ecu`, `cu`, `mcu`, `ms`, `fs`, `ws`, `ews`, `two` (frames two actors), `ots`
(over the first actor's shoulder onto the second), `insert` (with `at` a point).
`on`: actor or list. `side`: degrees around the subject from their front (+ = their left).
`relative`: `body` (default), `face` (follow the head's turn) or `world` with `world_yaw`.
`height`: `eye`, `slight_high`, `high`, `top`, `slight_low`, `low`, `worm`.
`move`: `drift` (default), `static`, `push`, `pull`, `push_fast`, `arc`, `rise`, `track`.
`key`: the fraction of the shot the framing is solved for (default 0.6), `lens`,
`dist_mul`, `lead`, `fstop`, `locked` (no automatic angle search).

Sizes are defined in studs of visible frame height because a 9:16 frame is width-limited:
a `cu` shows the head and shoulders. The solver keeps the eyes on the upper third, tries
up to ±65° and +20° of elevation when furniture or another actor blocks the face, keeps
the lens out of solid geometry and removes wild walls and ceilings for that shot only.
Every adjustment is written into the layout report.

## Staging rules learned the hard way

- **Hugs need a height difference** (scales ≥ 0.15 apart) and the receiver must face the
  other actor first (`stand` → `turn` → `open_arms` → `approach`).
- **Routes avoid furniture, not people**: never path between seated actors' knees.
- **A child's face during a hug is hidden.** Show the adult's face over the child's head,
  or break the hug into arm's length (`hold_shoulders`) for the child's reaction.
- **Turn the faces toward the open side** of the set (`head_turn` in hugs).
- **Close-ups need the face to point at the lens.** If the actor looks off at something,
  use `relative: face` or put the camera on that side.
- **Keep the story object in frame**: a counter above a head needs `mcu` or wider.
- **Do not cross the line** within a scene: QC warns `cut.axis` when two actors swap sides.
- **Something moves in every shot**: a push-in, a look, a blink is not enough for 3 s.

## Gate: `python3 scripts/studio.py layout SLUG`

Evaluates every frame exactly as it will render and fails on:

| Rule | Meaning | Usual fix |
| --- | --- | --- |
| `collision.set` | A body part is inside furniture or a wall | Change the mark, route or timing; check seat height |
| `collision.actor` | Two actors interpenetrate outside a declared contact | Use `approach` for embraces; open arms before the partner arrives; release in order |
| `feet.slide` / `feet.sink` | Planted foot moves or sinks | Do not teleport mid-shot; walking handles itself |
| `camera.solve` (error) | A close shot's face is blocked or out of frame | Change `side`, `height`, `size`, or restage |
| `film.variety` | Fewer than 3 shot sizes | Vary sizes |

Warnings (`collision.self` corners of an arm inside its own torso, adjusted cameras,
`cut.axis`) are listed for the director.

`--quick` checks every 2nd frame and skips `feet.slide` (it needs consecutive frames). Use it
while restaging; it is never recorded as the gate.

## Storyboard: `python3 scripts/studio.py board SLUG [--animatic]`

Renders each shot's key moment with Workbench in a few seconds and writes
`renders/SLUG/board/storyboard.jpg`, labelled with shot id, size, beat and line.
`--animatic` also renders every frame and cuts a scratch-sound animatic.

**Checkpoint.** The director looks at the sheet with the sound off and records it:

```sh
python3 scripts/studio.py review SLUG board --verdict approve|revise \
    --saw "sh01-04 hook reads; sh09 is the back of Ava's head; sh16 crops HE MISSES YOU ..."
```

Look for: can the story be followed muted, are faces turned to camera on the important
lines, do counters read, are there backs of heads, foreground blocks or crops. `--saw` needs
12+ words with shot ids. A full render and `make` stop until an approving review matches the
current sheet; any change that changes the sheet voids it. `render --unreviewed "why"`
bypasses it and is logged as a manual intervention for the retrospective.
