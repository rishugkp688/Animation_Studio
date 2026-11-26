# Story department — writer, story editor, director

The audience is scrolling. They decide in under two seconds and they watch on mute about
half the time. A good Blocky Studio short is a **complete emotional story in 45–65 seconds
that a viewer can follow from the pictures alone** and that lands harder with the sound on.

## The format that works (studied from the reference reels in `guides/`)

- **A world rule in the first line**: "In my town, everyone is born with a number of hugs."
  "In this town, a mirror shows who you'll become." The rule must be *visible* on screen
  (a number over every head, a mirror, a glowing house) within 2 seconds.
- **"…except me."** The narrator is the exception to the rule. That is the wound.
- **A person who seems wrong**: the cold grandpa, the strict teacher, the bully. The
  twist reveals the love or the reason behind them.
- **A choice that costs the hero something**, made on screen, followed by a short
  wordless reaction.
- **A last line that reframes the whole film**, ideally echoing a planted phrase
  ("ten years" → "ten seconds").

## Beat sheet for ~60 seconds (adjust to the voice timing)

| Beat | Seconds | Job | Example (One Hug Left) |
| --- | --- | --- | --- |
| hook | 0–4 | Show the rule working on someone else | A girl hugs her dad; both counters tick down |
| wound | 4–18 | The hero's exception and what it costs; a small ritual of love | Kai has 0; Mom's hug bounces off; she ruffles his hair instead |
| mystery | 18–30 | Plant the person who seems wrong and an unanswered question | Grandpa has had 1 hug left for 10 years. "Not yet, kiddo." |
| turn | 30–42 | Circumstances force the moment | Grandpa is sick. "Come here, kiddo." He stands up and hugs Kai |
| reveal | 42–50 | The rule nobody told you; the question is answered | Kai's 0 becomes 1. "You can only give it away." "It was always yours." |
| choice | 50–57 | The hero acts immediately, at a cost | Kai runs to Mom and spends it |
| button | 57–64 | One line that reframes it all; hold on the image | "Grandpa saved his for ten years. I saved mine for ten seconds." |

Declare each beat in `beats` with an `emotion` from −5 to +5. The gate requires at
least five beats, a range of 4 or more, and the first shot on the hook.

## Writing rules

1. **Every shot has something the camera can see change**: a walk, a look, a hug, a counter
   ticking, an expression. "Kai thinks about it" is not a shot.
2. **Lines are short.** At most 14 words, usually 4–9. Captions show 1–3 words at a time.
   Aim for 90–140 words per minute of film. Leave silence after the reveal.
3. **Plant and pay off at least two things** in `setups`. The best ones are visual (a blocked
   hug at the start, a completed one at the end) and verbal ("ten years" → "ten seconds").
4. **Three or more locations.** Movement between places is how a minute feels like a story.
   Use the set library (`kit/sets.py`) or ask Design for a new set before writing it.
5. **Give the lead at least six different expressions** across the film. The face *is*
   the performance in this style. Use the list in `kit/acting.py` (`EXPRESSIONS`).
6. **Dialogue is rare and earns its place.** Narration carries the story; one or two
   spoken lines in-scene ("Not yet, kiddo." / "Come here, kiddo.") are the moments.
7. **The ending is bittersweet, not tragic.** Imply loss; end on a warm image.

## Craft notes (why the rules exist)

- **Character = want, need, lie.** The want is what the hero chases; the need is what they
  lack; the lie is what stops them. Name the lie in one sentence and the on-screen action
  that proves it broke. If either is missing, the story is not ready. (Kai: "nobody can
  hug me" → he spends a hug on Mom.)
- **One object carries the film.** Use it in the first act and again at the end, from a
  similar angle, with a new meaning (the counter: 0 at the gate, 1 in the corridor).
- **Write what the camera sees.** No interior states in the action: no "he feels betrayed",
  only what a hand, a face, a light or a distance does.
- **Write for the rig.** The climax should be a held look, a hug, a counter changing or a
  light shift, not fast fluid action. Strong poses and timing beat busy motion.
- **Silence is a decision.** Music → quiet → line → effect → music. Drop the score before the
  line that matters; leave room tone under the reaction.
- **The usual AI failure modes and their counters:**

| Failure | Counter |
| --- | --- |
| Explains emotions instead of showing them | The camera-only rule above |
| Ties everything up neatly | Resolve the action, leave one question open |
| States the twist | Show the effect first; let the viewer work out the cause |
| The first idea is the average idea | Write 10–20 one-line premises, keep the most specific one that the set library can stage |

Order of work for an open brief: constraints (sets, cast, length) → premise batch → rule
test (literal, visible, one sentence, has a price) → character sheet → silent beat sheet →
lines last → critic pass in a fresh context.

## Writing the screenplay file

Start from `studio/templates/screenplay.json` (created by `studio.py new`). Per scene: the
set, time of day, ambience, starting marks/seats, starting expressions, looks and counters.
Per shot: `id`, `beat`, `lines` (ids, optionally `{"id": ..., "at": seconds}`), `cam`, and
`act`. The full action and camera vocabulary is in [03-layout-animation](03-layout-animation.md).
`dur` is optional; without it a shot lasts as long as its lines plus `pad`. A shot that should
stay completely still on purpose (a held breath, a stunned silence) says why with
`"hold": "..."`; the pacing audit then accepts frozen picture inside that shot. Use it rarely:
it is a decision, and the picture review sees it.

## Gate: `python3 scripts/studio.py story SLUG`

Checks: required sections, ≥ 3 locations (warns below 2.5 per minute), unique shot ids,
lines ≤ 14 words, voices exist, ≥ 5 beats with emotional range ≥ 4, setups before payoffs,
≥ 4 shot sizes, ≥ 6 lead expressions, every shot has action or a line, a length inside
`duration_s.limit` in `studio/quality-bar.json` (30–78 s; outside the 45–65 s target is a
warning the picture review must answer), every `hold` says why, ≥ 18 shots per minute,
≤ 165 words per minute, and a line within the first 0.6 s.

## Director's read-through (the part a script cannot check)

Read the lines aloud with a stopwatch, then answer in `notes.md`:

- **Mute test**: with the sound off, which shots tell the story? If the reveal only works
  through narration, restage it (a counter changing is visual; "he loved me" is not).
- **Hook test**: what does a stranger understand at 0:02? Write it down.
- **Why now?** Why does the turn happen in this episode and not earlier?
- **Who changes?** Name the change in the hero *and* in the person who seemed wrong.
- **Is the last line earned?** It should echo something planted in the first half.
- **Would I send this to a friend?** If the answer is "it's nice", find the sharper cost.

Then run the **critic pass**: a separate agent with only the screenplay and
`studio/story-rubric.json` (no knowledge of the intent) scores each dimension 0–3 and cites
the shot. Build only when every dimension is ≥ 2 and the mean ≥ 2.5. Taste notes say who judged.
