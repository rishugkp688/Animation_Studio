# Model-independence benchmark

Measures how well different models and agent harnesses **operate** the studio, so that
improvements to the engine's interfaces are judged by evidence, not by how capable the
model that wrote them was. Results: [results/README.md](results/README.md).

```sh
python3 benchmarks/bench.py run b2-repair --harness claude --model claude-haiku-5-5 --reps 2
python3 benchmarks/bench.py run b2-repair --harness codex  --model gpt-5.6-luna
BENCH_BASE_URL=https://api.deepseek.com/anthropic BENCH_API_KEY=... \
python3 benchmarks/bench.py run b2-repair --harness claude-compat --model deepseek-chat
python3 benchmarks/bench.py report            # results/runs.jsonl -> results/README.md
python3 benchmarks/bench.py setup b2-repair DIR   # the exact workspace an agent gets, for inspection
```

## Tasks

| Task | Kind | What the agent must do | Scored by |
| --- | --- | --- | --- |
| `b1-operate` | execution | Take a finished screenplay (One Hug Left, renamed) through story, voice, layout, storyboard and the board review. No rendering. | trusted story + layout gates, board built, approving review recorded, screenplay unchanged |
| `b2-repair` | recovery | Three Words with four injected staging faults (82 layout errors in 3 rules) must pass layout again without changing the story. Three Words is hidden from the workspace. | trusted layout gate = 0 errors, intent preserved (lines, cast, beats, shot list, sizes, subjects, expressions, badges, action verbs), number of edits, which faults were restored |
| `b3-brief` | creative | Write an original film from a one-paragraph brief and take it to an approved storyboard. | trusted story + layout gates, voice gate, board, review; the screenplay and board are kept for an artistic review by a person |

The faults in `b2-repair` (`tasks/b2-repair/faults.json`) mirror real mistakes from making
Three Words: a walk that ends inside furniture, a start mark inside a cabinet, a walk that
ends inside a seated actor, and a hug aimed at the wrong body part. A fifth candidate, a two-shot
from behind, was dropped: the camera solver already fixes it on its own.

Execution and creative tasks are separate on purpose: a weak result on `b3` alone means a
weaker filmmaker; a weak result on `b1`/`b2` means the studio is hard to operate.

## Protocol

**Frozen inputs.** Every run in a comparison uses one engine snapshot: a commit, or
`git stash create` of the working tree (a commit object that changes no branch or file),
pinned with `git update-ref refs/bench/NAME`. Each record stores the commit, its tree hash,
and a hash of the task files and preamble. The agent's workspace is an export of that
snapshot with **no git history** (so the original films cannot be recovered from it), the
source film hidden for `b2`, and nothing from `benchmarks/`, `renders/` or the reference reels.

**Isolated harness.** Claude Code runs with `--setting-sources project`, no MCP servers,
no web tools, no subagents, and with variables from any enclosing agent session removed.
Codex runs with a private `CODEX_HOME` holding only the login and the model, so the user's global
instructions do not apply. Both get the same prompt: `tasks/preamble-operator.md` + the
task. Reasoning effort is the harness default unless `--effort` is given; it is recorded.

**Operator mode** (the default): the engine, studio configuration, docs, tests and other
films are read-only, and the prompt forbids changing them. **Developer mode**
(`--mode developer`): the engine may change (and each change is counted); the quality bar
stays read-only in both modes.

**Integrity** (a run that violates any of these fails, whatever its gates say):
1. every protected file is hashed before and after; any change outside the agent's episode
   voids an operator run (developer runs: outside the engine and tests);
2. gates are re-run by a **separate trusted copy** of the frozen engine on the agent's
   screenplay; the agent's own stamps are never trusted;
3. every `ok` stamp in the agent's `status.json` must match a line in `history.jsonl`
   (a hand-written stamp is detected);
4. `--unreviewed` and other overrides are logged by the studio and visible in the history.

**Recorded per run** (`results/runs/RUN/record.json`, summary line in `results/runs.jsonl`):
model (requested and reported), harness and version, effort, mode, commit, tree, task hash,
elapsed time, timeout, tool calls by type and failed calls, turns, tokens (input, output,
cache read, cache write), cost when the harness reports it, every studio command the agent
ran with pass/fail and seconds (from the studio's own history), and the agent's final
report. The full transcript is kept gzipped. **Human interventions:** none are possible;
runs are headless with no input. If a run is ever assisted, record it in the run's notes.

**Repetitions.** Models are not deterministic. Run `--reps 2` or more and compare
distributions. A single run is an anecdote.

**Vision.** Some models cannot see images. The preamble tells them to say so in any review
rather than claim they looked; the review text is kept in the record so this can be checked.

## Known gaps

- Shell commands are not sandboxed for Claude Code runs (Codex uses its `workspace-write`
  sandbox). Read-only files, hashing and trusted re-scoring catch tampering. They do not
  stop an agent from writing elsewhere on the machine.
- Claude Code token counts for a run killed at its time limit are summed from per-message
  usage and may undercount output tokens (flagged in the record).
- The voice gate in `b3` is not re-run by the trusted scorer (it would re-synthesize every
  line); its stamp and history line are checked instead.
- Codex reports no cost; Claude Code reports a notional API cost even on a subscription.
