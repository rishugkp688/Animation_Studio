# Producing a film: story → animation → sound → delivery (agent skill)

This is the whole production pipeline of the studio, written so that an agent could make a
good film even if the code were lost: what to write, in what order, what each department
checks, which numbers a stage must hit before the next starts, and how to judge the result.
The reference film is *One Hug Left* (63.6 s, 4 locations, 24 shots, 7 characters,
18 voiced lines): `episodes/one-hug-left/screenplay.json`.

Companion files: [01-blender.md](01-blender.md) (rendering), [02-sound.md](02-sound.md)
(voice, foley, score, mix), [04-agent-guide.md](04-agent-guide.md) (how the engine works
inside, and lessons learned).

---

## 1. What we make, and for whom

- **Original, emotional, complete stories** of 45–65 s, vertical 1080×1920, 24 fps, in
  the Roblox R6 block style, for Instagram Reels (and TikTok/Shorts).
- The viewer is **scrolling**. They decide in under two seconds, and about half watch
  **muted**. So: the rule of the world must be *visible* in the first two seconds, every
  shot must show something change, and captions carry the words.
- The creator's bar: "if it cannot create a good story and animation then it is no use."
  Basic stories, objects passing through each other, one-room films and frozen actors
  are failures, not style. Original stories only: never recreate an existing film, game,
  character or reel, even renamed.

---

## 2. The studio: roles, stages and gates

One agent can wear every hat, but **one hat at a time**, in order. A stage is finished
when its gate passes and the next department can work without asking questions.

| # | Department (roles) | Output | Gate (blocks the next stage) |
| --- | --- | --- | --- |
| 1 | **Production** (producer, PM, TD) | brief in `notes.md`, episode folder | scope fits the machine: 45–65 s, 3–5 locations, 2–4 leads + extras, 20–28 shots |
| 2 | **Story** (writer, story editor, director) | `screenplay.json`: cast, voices, lines, beats, setups, music, scenes, shots | story lint passes (§4.6); director's read-through answered; critic score ≥ 2 each, mean ≥ 2.5 |
| 3 | **Design** (art director, character/prop/set designers) | cast looks, wardrobe, sets in the library | every character readable at phone size by silhouette + one unique colour; every set has a landmark |
| 4 | **Voice** (casting, voice actors) | takes + `voice/timing.json` | every take understood by ASR (match ≥ 0.8) |
| 5 | **Storyboard / layout / animation** (storyboard artist, previs, layout, rigger, animator) | board sheet, solved plan, layout report | **zero** collision/foot/camera errors over **every frame** |
| 6 | **Lighting & render** (lighting, FX, render wrangler) | PNG frames | every frame present, darkest shot checked |
| 7 | **Sound** (sound designer, composer, mixer) | stems, `mix.wav`, `cues.json` | every line understood through the full mix (worst ≥ 0.6) |
| 8 | **Post** (editor, compositor/colourist, captions, delivery QC) | captioned mp4, clean mp4, contact sheet | H.264/yuv420p, 1080×1920, 24 fps, AAC, decodes to the end, −14 ± 1.5 LUFS, TP ≤ −1 dBTP, pacing audit |
| 9 | **Director's review** | notes in `notes.md` | an honest written review of what was seen; human listening/viewing still pending is stated |

Commands (`scripts/studio.py`):

```sh
python3 scripts/studio.py new SLUG --title "Title"
python3 scripts/studio.py story SLUG
python3 scripts/studio.py voice SLUG [--force LINE_ID ...]
python3 scripts/studio.py board SLUG [--animatic]
python3 scripts/studio.py layout SLUG [--quick]
python3 scripts/studio.py render SLUG --preset preview|final|final_hq [--frames A-B]
python3 scripts/studio.py sound SLUG --preset ...
python3 scripts/studio.py deliver SLUG --preset ...
python3 scripts/studio.py make SLUG --preset preview|final       # all stages; stops at the first failed gate
python3 scripts/studio.py status SLUG                            # pass/fail per stage, "stale" if inputs changed
```

Time budget on the GTX 1650 for a 60 s film: voice ~4 min, layout QC ~1 min, build
~25 s, preview render ~10–13 min, sound ~30 s, final render ~75 min (3 s/frame).

**Iteration loop.** Story → voice → board → layout until zero errors → preview → watch →
notes → fix in the screenplay (or the kit) → preview again → final. Never polish a
final before a preview has been reviewed.

---

## 3. Producer: the brief

Write this at the top of `episodes/SLUG/notes.md` before any story work:

```
Logline: In a town where <visible rule>, <hero> who <is the exception> learns <truth> when <event>.
Length: 60 s ± 5. Delivery: Instagram Reels 1080x1920, captions burned in + clean copy.
Narrator: the hero as an adult looking back (first person), 1–3 spoken lines in scenes.
Cast: 2–4 leads + extras. Locations: 3–5 (reuse the set library; budget a design pass per new set).
Visual rule: <what the camera sees that proves the rule> (e.g. a counter over every head).
```

Constraints the story must respect (write them down, they shape the idea):

- What the rig does well: walking, running, turning, sitting/standing, hugs, hands on
  shoulders, ruffling hair, pointing, reaching, waving, head down, sobbing, nodding,
  shrugging, flinching, 25 facial expressions with tears and blush, overhead counters,
  simple glow/ripple FX.
- What it does badly: fast fluid action, fights, object hand-offs with precise grips,
  crowds of more than ~8, vehicles moving, water, cloth.
- **Write the climax as a held look, a hug, a counter changing or a light shift**, not as action.

---

## 4. Story: how to write a story that holds

### 4.1 The format that works (from the reference reels in `guides/`)

1. **A world rule in the first line**, shown on screen within 2 s. "In my town, everyone
   is born with a number of hugs." "In this town, a mirror shows who you'll become."
2. **"…except me."** The narrator is the exception; that is the wound.
3. **A person who seems wrong**: the cold grandpa, the strict teacher, the bully. The twist
   reveals the love or the reason behind them. *The twist is a person, not a monster.*
4. **A turn** forced by circumstance (sickness, a deadline, a loss).
5. **A reveal**: a rule nobody told you, or what the rule actually meant.
6. **A costly choice** the hero makes on screen, immediately, followed by a short
   wordless reaction.
7. **A last line that reframes the film**, echoing a phrase planted early
   ("ten years" → "ten seconds").

### 4.2 Beat sheet for ~60 s

| Beat | Seconds | Job | One Hug Left |
| --- | --- | --- | --- |
| hook | 0–4 | show the rule working on someone else | a girl runs into her dad's arms; both counters tick down with a chime |
| wound | 4–18 | the hero's exception and what it costs; a small ritual of love | Kai's counter is 0; Mom's hug bounces off an invisible wall; she ruffles his hair instead |
| mystery | 18–30 | plant the person who seems wrong + an unanswered question | Grandpa has had 1 hug for 10 years, looks at Grandma's photo. "Who's it for?" "Not yet, kiddo." |
| turn | 30–42 | circumstances force the moment | Grandpa is sick. "Come here, kiddo." He stands and hugs Kai |
| reveal | 42–50 | the hidden rule; the question answered | Kai's 0 becomes 1. "You can only give it away." "It was always yours." |
| choice | 50–57 | the hero acts at once, at a cost | Kai runs down the corridor and spends it on Mom |
| button | 57–64 | one reframing line; hold on the image | "Grandpa saved his last hug for ten years. I only saved mine for ten seconds." |

Give each beat an `emotion` from −5 to +5. The arc must range at least 4 points and
should flip sign at least twice (One Hug Left: +1, −2, −1, +3, −3, +5, +4).

### 4.3 Craft rules (why stories were flat before)

- **Character = want, need, lie.** Name the lie in one sentence and the on-screen action
  that proves it broke. If either is missing, the story is not ready.
- **One object carries the film**: used early, used again at the end with a new meaning
  (the counter: 0 at the gate, 1 in the hospital, spent in the corridor).
- **Write only what the camera sees.** No interior states in actions ("he feels betrayed");
  only what a hand, a face, a light, a distance or a counter does.
- **Plant and pay off at least two things**, ideally one visual (a blocked hug → a completed
  hug) and one verbal ("Not yet" → "Come here"). One Hug Left has five.
- **Misdirect honestly.** Grandpa looks at Grandma's photo while we hear he has been saving
  the hug: the audience assumes it is for her. The truth was always on screen (ten years =
  Kai's age).
- **Silence is a decision**: music → quiet → line → effect → music. Drop the score before the
  line that matters.
- **Bittersweet, not tragic.** Imply the loss (Grandpa's counter goes to 0 in a hospital);
  end on a warm image.
- **Dialogue is rare.** Narration carries the story; 2–4 spoken lines in scene are the moments.

AI failure modes and their counters:

| Failure | Counter |
| --- | --- |
| Explains emotions instead of showing them | camera-only actions; the mute test |
| Ties everything up neatly | resolve the action, leave one question open |
| States the twist | show the effect first, let the viewer infer the cause |
| The first idea is the average idea | write 10–20 one-line premises, keep the most specific one the set library can stage |

### 4.4 Writing order for an open brief

1. Constraint sheet (sets available, cast budget, rig limits, length).
2. Premise batch: 10–20 one-sentence premises. Keep three distinct ones.
3. Rule test: literal, visible, one sentence, has a price, hides a loophole in its wording.
4. Character sheet: want, need, lie, cost, the action that proves the lie broke.
   The hero must cause the turn or the choice.
5. Silent beat sheet with timestamps: every beat describable as something visible.
6. Shot list per scene (sizes, actions), then **lines last**.
7. Critic pass in a **fresh context** (a separate agent with only the screenplay and
   `studio/story-rubric.json`): score specificity, agency, dilemma, subtext, withholding,
   rule clarity, prop transformation, cost, open question, producibility, each 0–3. Build
   only at ≥ 2 everywhere and ≥ 2.5 mean. Revise the lowest two and re-score.

### 4.5 Line rules

- ≤ 14 words per line (usually 4–9); captions show 1–3 words at a time.
- 90–140 words per minute overall (One Hug Left: 106).
- A line within the first 0.6 s.
- Leave silence after the reveal and before the button.

### 4.6 The story gate (`studio.py story SLUG`)

Before voices exist, timing is estimated from word count (0.26 s + 0.30 s per word,
fitted on real takes). Record the voices early (`studio.py voice`) and re-run the gate on
real timing before judging pacing. The story gate does not evaluate poses: bad action
targets surface at `layout`.


Errors: missing sections (title, logline, theme, cast, voices, lines, beats, setups,
scenes); fewer than 3 locations; duplicate shot ids; a line over 14 words; a line with no voice; an unknown set;
fewer than 5 beats; emotional range < 4; first shot not the hook; undeclared beat; fewer
than 2 setups or a payoff before its setup; a shot without a camera size; fewer than 4
shot sizes; the lead shows fewer than 6 expressions; a shot where nothing happens;
duration outside 30–78 s; fewer than 18 shots per minute; more than 165 words per minute;
no line in the first 0.6 s. Warnings: unused lines, fewer than 2 emotional flips, a shot
longer than 6 s, a hook longer than 4.5 s, fewer than 2.5 locations per minute,
mispronounced takes.

### 4.7 Director's read-through (what a script cannot check)

Read the lines aloud with a stopwatch, then answer in `notes.md`:

- **Mute test**: with sound off, which shots tell the story? A counter changing is visual;
  "he loved me" is not. Restage beats that only work through narration.
- **Hook test**: what does a stranger understand at 0:02?
- **Why now?** Why does the turn happen in this episode and not before?
- **Who changes?** The hero *and* the person who seemed wrong.
- **Is the last line earned?** Does it echo a plant?
- **Would I send this to a friend?** If it is only "nice", find a sharper cost.

---

## 5. The screenplay file (the single source of truth)

Everything downstream is generated from `episodes/SLUG/screenplay.json`. Full schema:

```jsonc
{
  "title": "One Hug Left",
  "logline": "...", "theme": "...", "audience": "Instagram Reels / TikTok, phone in hand.",
  "style": "roblox-r6", "fps": 24,
  "narrator": "kai",                         // the lead; used for the 6-expression check
  "cast": {
    "kai": {"name": "Kai", "role": "lead, age 10, narrator",
            "scale": 0.8,                   // children 0.72-0.82, adults 0.95-1.0
            "build": 1.0,                   // torso width 1.0-1.08
            "look": {"skin": "#E9BE95",
                     "hair": {"style": "spiky", "color": "#2B211D"},   // spiky|short|bob|long|pigtails|bun|bald_sides
                     "glasses": "#3B3B3B", "mustache": "#D4D4D2", "beard": "#1F1A18", "wrinkles": true, "freckles": true,
                     "outfits": {"default": {"top": {"style": "hoodie", "color": "#3D72D9", "trim": "#E9EEF7"},  // hoodie|cardigan|shirt|sweater|tee|gown
                                             "pants": "#24272E", "shoes": "#F1F1F1"}}},
            "badge": true,                  // overhead counter
            "caption_color": "#FFE0A6"}    // optional dialogue caption tint
  },
  "voices": {"kai": {"engine": "qwen3", "speaker": "aiden", "seed": 211, "instruct": "..."},
             "kai_child": {"engine": "qwen3", "speaker": "aiden", "seed": 307, "pitch": 2.5, "instruct": "..."}},
  "lines": [{"id": "n01", "who": "kai", "kind": "vo", "text": "In my town, everyone is born with a number of hugs."},
            {"id": "k01", "who": "kai", "voice": "kai_child", "kind": "dialogue", "text": "Who's it for, Grandpa?",
             "say": "Who is it for, Grandpa?", "gain": 0.0}],
  "beats": [{"id": "hook", "purpose": "...", "emotion": 1}],
  "setups": [{"id": "zero", "setup": "sh03", "payoff": "sh16", "note": "Kai's 0 becomes 1."}],
  "music": [{"at": "sh01", "mood": "warm"}, {"at": "sh15", "mood": "swell", "offset": 0.6}],
  "scenes": [{
    "id": "sc1", "set": "school_gate", "time": "morning", "beat": "hook", "ambience": "schoolyard",
    "wardrobe": {"grandpa": "gown"},                       // outfit per actor for this scene
    "cast": {"kai": {"at": [0.55, 3.6], "yaw": 180},       // or {"mark": "door_in"}, {"seat": "armchair"}, {"contact": "nina_mom"}
             "nina": {"contact": "nina_mom"}},             // placed at hug distance in front of nina_mom
    "start_badge": {"kai": "0"}, "start_expr": {"kai": "neutral"}, "start_look": {"mom": "kai"},
    "shots": [{
      "id": "sh01", "beat": "hook",
      "lines": ["n01"],                                     // or [{"id": "n10", "at": 1.55, "gap": 0.2, "spill": true}]
      "pad": 0.2, "min": 1.6, "dur": null, "lead": 0.25,    // shot length: lines + pad, or an explicit dur
      "cam": {"size": "fs", "on": "lily_dad", "side": -12, "height": "slight_high", "move": "push", "key": 0.65,
              "relative": "world", "world_yaw": -90, "dist_mul": 1.3},
      "act": [
        {"who": "lily", "t": 0.0, "do": "approach", "run": true, "to": "lily_dad", "look": "lily_dad"},
        {"who": "lily_dad", "t": 0.0, "do": "open_arms", "dur": 1.45, "raise": 1.5, "spread": 0.5, "expr": "joy"},
        {"who": "lily", "t": "+0.3", "do": "hug", "with": "lily_dad", "at": "hips", "lift": 0.12, "dur": 30, "head_turn": -1.0},
        {"who": "lily_dad", "t": 1.3, "do": "hug", "with": "lily", "at": "head", "lift": 0.12, "lean": 0.04, "dur": 30},
        {"who": "lily_dad", "t": 1.75, "do": "badge", "text": "351"},
        {"who": "kai", "t": 0.3, "do": "walk", "to": [0.7, 1.4], "look": "lily", "dur": 2.4}
      ]}]
  }]
}
```

### Action timing

`"t": 1.2` = seconds into the shot; `"line:n04+0.3"` = 0.3 s after line n04 starts;
`"end:g01-0.2"` = 0.2 s before line g01 ends; `"+0.3"` = 0.3 s after this actor's previous
action ended. Any action may also carry `"expr"` (with optional `expr_delay`) and `"look"`.

### Action vocabulary

| `do` | Parameters | Use |
| --- | --- | --- |
| `walk`, `run` | `to` (mark, seat, actor, `[x, y]`), `face`, `face_offset`, `speed`, `dur`, `backward`, `gap`, `direct` | routes avoid furniture; `dur` fits a beat |
| `approach` | `to` actor, `side` (0 = in front of them), `offset`, `run` | stops at the exact embrace distance for both bodies |
| `turn` | `to` actor/mark/point/yaw, `dur` | two small steps in place |
| `sit` / `stand` | `seat` / `to` | walks to the seat if needed |
| `crouch` / `uncrouch` | `dur` | |
| `look` | `at` actor, point, `[x, y, z]`, `cam`, `none` | eyes lead, head follows in 0.38 s |
| `badge` | `text`, `sfx` | counter pops, sparkles, chimes |
| `fx` | `kind: ripple`, `at` | invisible-wall ripple |
| `release` | `gesture` | ends a long gesture now |
| `sfx` | `kind` | adds a sound event |
| gestures | `dur`, plus per gesture: | |
| `hug` | `with`, `at` (`hips` for the shorter, `head` for the taller), `lift`, `lean`, `head_turn`, `head_pitch`, `extra_width`, `depth` | |
| `open_arms` | `spread`, `raise` (≥ 1.4 before someone runs in) | open before a hug closes |
| `hold_shoulders` | `with`, `lift`, `head_pitch`, `lean` | arm's-length beat after a hug |
| `ruffle`, `hold_hand` | `with`, `hand`, `their` | |
| `point`, `reach` | `at` / `to`, `hand` | |
| `wave`, `offer` | `hand`, `both` | |
| `hands_face`, `sob`, `head_down` (`amount`, `lean`), `nod`, `shake_head`, `shrug`, `flinch`, `hand_chest`, `think`, `look_around`, `lean_forward` (`amount`), `hands_behind`, `blocked_hug` (`stop`), `crouch` | | |

Expressions: neutral, calm, happy, joy, tender, sad, hurt, teary, cry, sob, cry_smile,
teary_smile, shock, surprise, worried, scared, angry, grumpy, determined, sleepy, smirk,
shy, hopeful, eyes_closed, peaceful.

### Camera vocabulary

| Key | Values |
| --- | --- |
| `size` | `ecu`, `cu`, `mcu`, `ms`, `fs`, `ws`, `ews`, `two` (two actors), `ots` (over the first actor's shoulder onto the second), `insert` (with `at`, `yaw`, `dist`) |
| `on` | actor or list of actors |
| `side` | degrees around the subject from their front; + = their left |
| `relative` | `body` (default), `face` (follow the head), `world` (+ `world_yaw`) |
| `height` | `eye`, `slight_high`, `high`, `top`, `slight_low`, `low`, `worm` |
| `move` | `drift` (default), `static`, `push`, `pull`, `push_fast`, `arc` (+`arc` deg), `rise`, `track` (follows per frame) |
| other | `key` (0–1, when in the shot the framing is solved, default 0.6), `lens`, `dist_mul`, `lead`, `raise`, `fstop`, `handheld`, `locked` |

---

## 6. Design

- **True R6 bodies** (legs 1×1×2 studs, torso 2×1×2, arms 1×1×2, rounded-cylinder head;
  1 stud = 0.33 m). Never change proportions per shot.
- **Decal faces**: ink eyes with moving pupils and brows, 16 eye shapes, 16 mouths
  (including talk shapes), tears, tear drop, blush. The face *is* the performance.
- **Silhouette and colour**: every lead has one colour nobody else wears (Kai is the only
  blue hoodie). Hair shapes differ between leads. Fringes stop above the brows.
- **Wardrobe changes** tell time and place (Grandpa's hospital gown).
- **Sets are data** (see `04-agent-guide.md` §6). Rules: R6 scale (shoulders are 1.3 m
  wide at scale 1: doors ≥ 1.35 m, aisles ≥ 1.5 m, chairs ≥ 1 m apart); one landmark per
  set (school arch, green armchair + lamp, hospital bed + monitor); removable walls flagged
  `cut`; colliding objects `solid`; seats tagged; motivated lights; a time-of-day preset.
- Before writing scenes in a new set: render an occupied wide and a close-up with
  `--stills` and look at them at phone size.

Library: `school_gate` (morning or afternoon exterior), `grandpa_living` (afternoon interior),
`hospital_room` (night), `hospital_corridor` (night). New stories will need new sets:
classroom, bedroom, kitchen, street, park, bus are the obvious next ones.

---

## 7. Layout and animation: staging that works

Animation is **written as actions in the screenplay and solved by the engine**; nobody
keys poses. The animator chooses actions, timing and acting beats, reads the QC report,
and fixes causes.

Staging rules learned on One Hug Left:

- **Something visible changes in every shot**: a walk, a turn, a gesture, an expression,
  a counter. A push-in plus a blink is not enough for 3 s.
- **Hugs need a height difference.** The hug gesture holds the taller actor high and the
  shorter low; with scales closer than ~0.15 apart the arms cross and QC fails. Make the
  older character clearly taller (Three Words: Leo 0.76, Ava 0.95), or use `hold_shoulders`.
- **Before a hug the receiver must face the other actor**: `stand` → `turn` to them →
  `open_arms` → the other `approach`es.
- **Walking routes avoid furniture, not people.** A path between two seated actors' knees
  collides with their legs. Route around people with intermediate marks, or stop beside them.
- **Every hug is staged in three steps**: the receiver opens the arms (`open_arms`,
  `raise` ≥ 1.4); the other actor `approach`es (exact embrace distance); the hugs start a
  moment apart (child first, adult 0.2–0.5 s later). The taller actor holds `at: head`,
  the shorter `at: hips`, both with `lift` ≈ 0.12. Release in order: child first, step
  back, then the adult.
- **A child's face is hidden in a hug.** Show the adult's face over the child's head, or
  break to arm's length (`hold_shoulders`) for the child's reaction (that is how the 0 → 1
  reveal became readable).
- **Turn faces toward the open side** of the set (`head_turn` in hugs).
- **Close-ups need the face pointed at the lens**: use `relative: face` or put the camera
  on the side the actor looks.
- **Keep the story object in frame**: an overhead counter needs `mcu` or wider.
- **Respect the 180° line** within a scene; QC warns when two actors swap screen sides.
- **Do not cut between two shots of the same subject at the same size and angle** (jump
  cut); change size by two steps (MS → ECU) or the angle by ≥ 20°.
- **Vary sizes**: wide to establish each place, medium for action, close for reactions,
  ECU for the reveal. One Hug Left uses ws, fs, ms, mcu, cu, ecu, two.
- **Check the storyboard for backs of heads.** Long hair seen from behind, a shot from
  behind the speaker, an over-the-shoulder blocked by long hair, a bowed head from above
  that hides the counter: all pass QC and all fail the audience. Fix with `side`,
  `relative: face`, a profile `two` (side 0) or a wider size / `dist_mul`.
- **Motivate every entrance**: actors walk in from doors or off frame; nobody pops into place mid-shot.
- Look at the hero's eyes, not the words: tears (`teary`, `cry`) on the turn, a smile
  through tears (`cry_smile`) on the payoff.

### The layout gate (`studio.py layout SLUG`)

Every frame is evaluated exactly as it will render. Errors (block rendering):

| Rule | Meaning | Usual fix |
| --- | --- | --- |
| `collision.set` | a body part inside furniture or a wall (> 2.5 cm) | change the mark, route or timing; seat height |
| `collision.actor` | two actors interpenetrate outside a declared contact | `approach` for embraces; open arms first; release in order |
| `feet.slide` | a planted foot moved > 2.2 cm in a frame | no teleports mid-shot |
| `feet.sink` | feet > 3 cm under the floor | floor height, crouch |
| `camera.solve` | a close shot's face blocked/out of frame, or the lens inside geometry | change `side`, `height`, `size`, or restage |
| `film.variety` | fewer than 3 shot sizes | vary sizes |

Warnings to note in the review: `collision.self` (arm corners inside own torso), camera
adjustments, `face.caption` (eyes near the caption band), `cut.axis`, `cut.jump`,
`feet.hover`. One Hug Left shipped with 0 errors and 15 warnings, each noted.

### Storyboard

`studio.py board SLUG` renders each shot's key moment in Workbench in seconds and writes a
labelled sheet `renders/SLUG/board/storyboard.jpg` (shot id, size, beat, line). The
director reads it with the sound off: can the story be followed from the pictures?
`--animatic` also renders every frame and cuts a scratch-sound animatic.

---

## 8. Render

Preview first (`--preset preview`, 540×960, ~0.4–0.5 s/frame), review, then final
(1080×1920, ~3 s/frame). Frames go to `renders/SLUG/<preset>/frames/f_####.png` and are
skipped if they exist, so a render resumes; **delete the frames of changed shots before
re-rendering them**. Look at the darkest shot first. Details in `01-blender.md`.

## 9. Sound

`studio.py sound SLUG --preset P` builds ambience beds, foley from the animation events,
the score from the music cues, the dialogue placement, ducking and the −14 LUFS master,
then runs the listening gate. Details and recipes in `02-sound.md`.

## 10. Post and delivery

`studio.py deliver SLUG --preset P`:

1. Builds word-timed captions (`captions.ass`) from the aligned lines.
2. Encodes `SLUG-P.mp4` (captions burned in) and `SLUG-P-clean.mp4`: H.264 High, CRF 17
   (final) / 20 (preview), yuv420p, `+faststart`, AAC 192 kbps 48 kHz.
3. Checks the actual file: codec h264 + yuv420p, exact dimensions, 24/1 fps, an audio
   stream, a full decode without errors, integrated loudness −14 ± 1.5 LUFS, true peak
   ≤ −1 dBTP, duration within 0.25 s of the plan.
4. Pacing audit (`scripts/video_audit.py` against `studio/quality-bar.json`): ≥ 18 cuts per
   minute, median shot ≤ 3.2 s, longest ≤ 6 s, ≤ 15 % static shots, ≤ 1.5 s frozen, first
   second motion ≥ 1.0, mean motion ≥ 2.0. (Reference reels: 13–41 cuts/min, mean motion
   3.6–10.6. One Hug Left: 21.7 cuts/min, median 2.25 s, mean motion 3.1, first second 0.65
   — below target, the one open pacing miss.)
5. Contact sheet `frames.jpg`, one frame per shot.

Edit rhythm: 1.5–3 s per shot; the hook under 4.5 s; hold 1.5–2 s after the reveal and on
the final image. To retime, change `pad`, `dur` or a line's `at` in the screenplay; never
trim rendered frames by hand.

## 11. Director's review (always write it)

Look at `frames.jpg`, extract single frames at key moments (hook, reveal, button), and
watch the preview at phone size twice: muted, then eyes closed. Write in `notes.md`, one
note per issue:

```
sh16 0:43.1 — Kai's counter is out of frame when it flips to 1. Layout: widen to mcu or raise
the camera. Retest sh16 at its key frame.
```

Include: shot id, time, what is wrong *for the audience*, which department owns it, how it
will be checked. State plainly what nobody has done yet (listening by ear, watching on a
phone). Posting to Instagram is the creator's decision and needs their explicit go-ahead.

## 12. Definition of done

All gates pass (story, voice, zero layout errors, every frame rendered, every line
understood through the mix, delivery QC on the actual file). The director's review is
written. The report to the creator gives the movie path, the clean version, the contact
sheet, the rebuild command (`python3 scripts/studio.py make SLUG --preset final`), the
measured numbers, and an honest list of open issues.

## 13. Checklist for the next film

- [ ] Brief and constraints in `notes.md`
- [ ] 10–20 premises → 3 → 1; rule test; character sheet with want/need/lie
- [ ] Beat sheet with emotions; ≥ 2 setups/payoffs; ≥ 3 locations; 20–28 shots
- [ ] Lines last; ≤ 14 words; ~90–140 wpm; hook line at 0 s
- [ ] Critic pass in a fresh context ≥ 2 each, ≥ 2.5 mean
- [ ] Story gate passes
- [ ] Cast designed (unique colour per lead), sets exist or designed and test-rendered
- [ ] Voices cast with specific instructions; all takes ≥ 0.8
- [ ] Board read muted; layout 0 errors; warnings noted
- [ ] Preview rendered, reviewed muted and with sound; notes fixed
- [ ] Final rendered; sound gate; delivery gate
- [ ] Director's review + honest report; no posting without the creator's go-ahead
