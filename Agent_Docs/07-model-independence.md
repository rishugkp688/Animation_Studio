# Model independence: baseline, findings and what changed

Can a cheaper model operate the studio as well as Opus? This answers that by measurement:
30 headless agent runs across 5 models and 2 harnesses, three tasks, two engine versions.
Protocol, task definitions and integrity rules: [benchmarks/README.md](../benchmarks/README.md).
Every run, with transcript, score and the agent's own report: `benchmarks/results/runs/`.
Table: [benchmarks/results/README.md](../benchmarks/results/README.md).

## Setup in one paragraph

Each run gets a history-free copy of a frozen engine snapshot (`refs/bench/baseline-20261127`,
then `refs/bench/round2-20261127`), the same prompt (operator mode: only the episode may change),
no human, and a time limit. Gates are re-run afterwards by a separate untouched copy of the
same engine; the agent's own stamps are never trusted; protected files are read-only and
hashed. Models: Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 (Claude Code 2.1.332) and GPT-5.6-luna,
GPT-5.6-sol (Codex CLI 0.146.0, the two this account can use). DeepSeek was not run: no key on
this machine; the `claude-compat` harness takes any Anthropic-compatible endpoint.

| Task | What it measures |
| --- | --- |
| `b1-operate` | running a finished film to an approved storyboard (execution) |
| `b2-repair` | recovering Three Words from 4 injected staging faults, 82 layout errors (recovery) |
| `b3-brief` | writing an original film from a one-paragraph brief to an approved storyboard (creative) |

## Baseline results (engine as it was)

All 19 baseline runs with vision passed every gate with integrity intact (after a scoring fix: Sonnet's operate run first counted as a fail for revising a camera after its own review, which the task allows).

| Task | Model | Time | Layout runs (failed) | Edits | Tokens in | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| operate | Haiku | 4.0 min | 1 (0) | — | 0.4 M | $0.01 |
| operate | luna | 6.5 | 1 (0) | — | 0.9 M | — |
| operate | Sonnet | 7.7 | 4 (1) | revised one camera after its own review | 0.9 M | $0.37 |
| operate | sol | 4.8 | 1 (0) | — | 1.0 M | — |
| operate | Opus | 3.8 | 1 (0) | — | 0.7 M | $0.66 |
| repair ×2 | Haiku | 12.2 / 16.9 | 3 (2) / 5 (4) | 4 / 4 | 2.5 / 2.4 M | $0.05 / $0.14 |
| repair ×2 | luna | 12.1 / 9.9 | 5 (4) / 8 (7) | 5 / 6 | 3.9 / 3.5 M | — |
| repair ×2 | Sonnet | 12.0 / 12.0 | 11 (8) / 12 (10) | 6 / 6 | 1.1 / 1.5 M | $0.41 / $0.52 |
| repair ×2 | sol | 10.1 / 8.9 | 9 (7) / 7 (6) | 4 / 7 | 7.8 / 5.6 M | — |
| repair | Opus | 10.5 | 9 (7) | 6 | 1.3 M | $0.90 |
| brief | Haiku | 28.8 | 15 (9) | — | 7.4 M | $0.72 |
| brief | luna | 17.3 | 5 (4) | — | 5.3 M | — |
| brief | Sonnet | 33.2 | 12 (4) | — | 6.9 M | $2.21 |
| brief | sol | 17.6 | 7 (5) | — | 8.0 M | — |
| brief | Opus | 25.7 | 9 (4) | — | 7.4 M | $4.76 |

Costs are what Claude Code reports (notional on a subscription); Codex reports none.

### What the baseline says

1. **The weakest models can operate the studio.** Haiku and luna passed all three tasks.
   Model size did not predict efficiency: on repair, Haiku used the fewest layout runs and
   edits; Sonnet and Opus used two to three times as many runs, at 4-15× the cost.
2. **Recovery from staging faults is already easy.** Every repair run found all four broken
   fields from the layout gate's messages alone; none needed the original film (hidden).
   They chose nearby values rather than the originals and never touched the story.
3. **The creative task is where time and tokens go,** and most of it is actor-on-actor
   collisions (hugs) and camera framing. Haiku's 9 failed layout runs on the brief were all
   collisions between a child and her father, solved in the end by copying One Hug Left's hug
   values.
4. **Agents can draw wrong conclusions from unchecked pictures.** Sonnet built a storyboard
   after an edit without re-running layout, saw a frame filled by a gown and
   reported an engine bug ("a blank wall layout did not catch"). Layout did catch it; the
   studio let a board be built and reviewed from staging that had not passed.
5. **Honesty held.** Every review that had vision said what it looked at; Haiku without
   vision refused to approve (below). No run edited a protected file or forged a stamp.

## What was built from those findings

| Finding | Change | Test |
| --- | --- | --- |
| 4 | `board` warns and **a board review cannot approve** unless the layout gate passed on the current screenplay | `test_board_approval_needs_a_current_passing_layout` |
| no-vision review impossible | **shot report** `reports/shots.txt`: per shot, who is on screen, fill, facing, what blocks a face, flags (`kit/shotreport.py`) | `ShotReport` (reproduces Sonnet's sh16 case) |
| 3 | **hug defaults by height**, taken from all 14 hand-tuned hugs: state only `with` and `dur` | `HugDefaults` (Three Words passes with every hug value removed) |
| agents grep source for names | the story gate lists valid names; **`catalog`** and **`schema`** read the vocabulary from the engine; the story gate validates against the schema first | `Catalog`, `test_unknown_names_list_the_valid_ones` |
| docs are 3,000 lines | **`OPERATOR.md`**, one page | — |
| recovery requirement | **`repair`**: bounded deterministic search, proposes, applies on request, undoable | `Repair` |
| Codex sandbox hang | headless Blender gets the null audio driver | `Headless` |
| crash found by repair | `Nav.route` never returns an empty path | `test_route_to_a_blocked_point_beside_the_actor_is_never_empty` |

All of it keeps both films' compiled plans byte-identical.

## Round 2 (engine with the changes)

| Task | Model | Baseline | Round 2 |
| --- | --- | --- | --- |
| operate, **no vision** ×2 | Haiku | honest refusal both times: "NOT VIEWED … verdict revise until a person looks" (cannot finish) | reviewed from `shots.txt`, said so, **approved; both runs flagged sh15** (Kai's teary face behind Grandpa's torso), the same weak shot Sonnet found by looking |
| brief | Haiku | 28.8 min, 15 layout runs (9 failed), 7.4 M tokens, $0.72 | 27.1 / 20.4 min, 4 (2) / 10 (6), 2.3 / 3.1 M, $0.10 / $0.23 |
| brief | luna | 17.3 min, 5 (4), 5.3 M | 11.6 min, 3 (1), 3.5 M |
| repair ×2 | Haiku | 12.2 / 16.9 min, 4 edits | **28.2 / 32.3 min, 8-9 edits: worse** |
| repair ×2 | luna | 12.1 / 9.9 min, 5 / 6 edits | 32.6 / 10.2 min, 6 / 11 edits: worse in one run, mixed in the other |

- **The shot report works.** A text-only model went from "cannot review" to a review that
  found the real problem, at the same cost ($0.01).
- **The brief got cheaper and needed fewer failed layout runs** for both weak models (Haiku
  tokens -58 to -69 %, luna time -33 %). Two or three runs per arm is too few to call it
  proven; it points the right way.
- **`repair` made weak agents slower.** All four round-2 repair runs passed, but three of them
  took longer than in the baseline. Both Haiku runs started the search, which ran past
  their shell command's time limit (it compiled every candidate on 4 CPU processes for 15+
  minutes), never got its result, then fixed the faults by hand anyway. Since then: the search
  has a time limit (`--max-seconds`, default 240) and prints progress, defaults to 2
  processes, and the operator card says to fix by hand first and use repair when stuck. It has
  not been re-measured.

## Deterministic repair, measured

On the `b2-repair` fixture, through the CLI: **79 → 10 collision errors**, fixing the three
independent faults (walk into a side table, start mark inside a cabinet, walk into a seated
actor) with one field each. It could not fix the fourth: Leo's sh18 stop and the sh20 hug that
follows must change together, and every candidate within its 0.8 m bound strands Leo where no
hug contact works (the original spot that works is 1.37 m away). It reports that group as
unresolved instead of forcing it. Every agent, even Haiku, solved all four. Conclusion: repair
handles independent mechanical faults; interacting faults stay the agent's job.

## Limits of these numbers

- One to two runs per cell. Distributions need more repetitions.
- Two harnesses only (Claude Code, Codex); DeepSeek and GLM not run.
- The **creative quality** of the eight brief films is not scored. Each run folder keeps
  the screenplay, storyboard and shot report for a person to judge
  (`benchmarks/results/runs/*b3-brief*/`). Gates passing means the film works technically, not that
  it is good.
- `b2-repair` turned out too easy to separate models; a harder recovery task (interacting faults,
  a broken voice line, a failed delivery) would say more.
- Round 2 changed several things at once; the no-vision result is attributable to the shot
  report, the brief improvement is not attributable to one change.
