#!/usr/bin/env python3
"""Model-independence benchmark for Blocky Studio.

Runs one agent (model + harness) on one task in an isolated, frozen copy of the studio, then
scores the result with a separate trusted copy of the same engine. See benchmarks/README.md.

    python3 benchmarks/bench.py run b2-repair --harness claude --model claude-haiku-5-5 --reps 2
    python3 benchmarks/bench.py run b2-repair --harness codex --model gpt-6.2-sol
    python3 benchmarks/bench.py setup b2-repair DIR      # build the frozen workspace only
    python3 benchmarks/bench.py score RUN_DIR             # (re)score a finished run
    python3 benchmarks/bench.py report                    # results/runs.jsonl -> results/README.md

Integrity, in order of strength:
  1. The agent's copy has no git history and hides the films the task is derived from.
  2. Protected paths are read-only during an operator run.
  3. Every protected file is hashed before and after; any change voids the run.
  4. Gates are scored by a trusted copy of the engine, never by the agent's stamps.
  5. Every ok stamp in the agent's status.json must have a matching history.jsonl line.
"""
import argparse
import copy
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks"
TASKS = BENCH / "tasks"
RESULTS = BENCH / "results"
WORK = Path(os.environ.get("BLOCKY_BENCH_WORK", ROOT.parent / (ROOT.name + "-bench")))

# Never copied into the agent's workspace.
EXCLUDE = ["renders", "archive", "guides", "benchmarks", ".agents", ".codex", ".commandcode"]
# Read-only and hashed in operator mode.
PROTECTED = ["AGENTS.md", "README.md", "Agent_Docs", "scripts", "studio", "tests", "episodes"]
# Never writable, in either mode: the bar the gates measure against.
GATE_FILES = ["studio/quality-bar.json"]
OPERATOR_TOOLS = ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
DENIED_TOOLS = ["WebFetch", "WebSearch", "Agent", "Task"]


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout.strip()


def load_task(tid, mode="operator"):
    d = TASKS / tid
    t = json.loads((d / "task.json").read_text())
    t["dir"] = d
    pre = (TASKS / f"preamble-{mode}.md").read_text().replace("{slug}", t["slug"])
    t["prompt"] = pre + "\n" + (d / "prompt.md").read_text()
    files = [TASKS / f"preamble-{mode}.md", *sorted(x for x in d.rglob("*") if x.is_file())]
    t["hash"] = hashlib.sha1(b"".join(f.name.encode() + f.read_bytes() for f in files)).hexdigest()[:12]
    return t


# ---------------------------------------------------------------- fixtures

def _find(sp, path):
    """Resolve a fault path: ["shot", id, "act", {match}, key] / ["scene", id, "cast", key]."""
    kind, ident, *rest = path
    if kind == "shot":
        node = next(s for sc in sp["scenes"] for s in sc["shots"] if s["id"] == ident)
    else:
        node = next(sc for sc in sp["scenes"] if sc["id"] == ident)
    parent, key = None, None
    for step in rest:
        parent = node
        if isinstance(step, dict):
            key = next(i for i, a in enumerate(node) if all(a.get(k) == v for k, v in step.items()))
        else:
            key = step
        node = node[key]
    return parent, key, node


def apply_faults(sp, faults):
    sp = copy.deepcopy(sp)
    for f in faults:
        parent, key, node = _find(sp, f["at"])
        if f.get("merge"):
            for k, v in f["was"].items():
                assert node.get(k) == v, (f["id"], k, node.get(k), v)
            node.update(f["set"])
        else:
            assert node == f["was"], (f["id"], node, f["was"])
            parent[key] = f["set"]
    return sp


def faults_restored(sp, faults):
    out = {}
    for f in faults:
        try:
            _, _, node = _find(sp, f["at"])
        except (StopIteration, KeyError, IndexError):
            out[f["id"]] = "removed"
            continue
        val = {k: node.get(k) for k in f["was"]} if f.get("merge") else node
        out[f["id"]] = "restored" if val == f["was"] else ("untouched" if val == f["set"] else "changed")
    return out


# ---------------------------------------------------------------- workspace

def fingerprint(base, rels):
    out = {}
    for rel in rels:
        p = base / rel
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts) if p.exists() else []
        for f in files:
            out[str(f.relative_to(base))] = hashlib.sha1(f.read_bytes()).hexdigest()
    return out


def set_writable(base, rels, writable):
    for rel in rels:
        p = base / rel
        if not p.exists():
            continue
        for x in [p, *p.rglob("*")] if p.is_dir() else [p]:
            if x.is_symlink():
                continue
            m = x.stat().st_mode
            os.chmod(x, (m | stat.S_IWUSR) if writable else (m & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)))


def export_tree(dest, commit, exclude):
    dest.mkdir(parents=True)
    spec = ["."] + [f":(exclude){e}" for e in exclude]
    arc = subprocess.run(["git", "-C", str(ROOT), "archive", commit, "--", *spec], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arc, check=True)


def setup(task, ws, commit):
    """Build the agent's workspace: a history-free export of `commit` plus the task fixture."""
    hidden = [f"episodes/{e}" for e in task["hide_episodes"]]
    export_tree(ws, commit, EXCLUDE + hidden)
    (ws / ".audio-tools").symlink_to(ROOT / ".audio-tools")
    (ws / ".gitignore").write_text((ws / ".gitignore").read_text() + "\n.audio-tools\n")
    slug, src = task["slug"], task["source_episode"]
    fixture = None
    if src:
        ep = ws / "episodes" / slug
        # The source film comes from the frozen commit, not the working tree.
        tmp = ws.parent / (ws.name + ".src")
        export_tree(tmp, commit, [])
        shutil.copytree(tmp / "episodes" / src / "voice", ep / "voice")
        sp = json.loads((tmp / "episodes" / src / "screenplay.json").read_text())
        shutil.rmtree(tmp)
        if task.get("faults"):
            sp = apply_faults(sp, json.loads((task["dir"] / task["faults"]).read_text()))
        (ep / "screenplay.json").write_text(json.dumps(sp, indent=1))
        (ep / "notes.md").write_text(f"# {sp['title']}\n\nDirector's notes go here.\n")
        fixture = sp
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="bench@localhost",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="bench@localhost")
    for c in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", f"frozen studio {commit[:10]} + {task['id']}"]):
        subprocess.run(c, cwd=ws, check=True, env=env)
    return fixture


# ---------------------------------------------------------------- harnesses

def harness_version(h):
    try:
        return sh([h, "--version"])
    except Exception:
        return "unknown"


def base_env():
    """The caller's environment minus anything from an enclosing agent session, so a run
    launched from inside Claude Code or Codex behaves like one launched from a plain shell."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_PID", "CLAUDE_EFFORT", "CODEX_"))}


def harness_cmd(h, model, prompt, ws, home, effort=None, no_vision=False):
    if h == "claude":
        # No vision: without the Read tool the agent can still read text through the shell
        # (cat, sed) but has no way to look at an image.
        tools = [t for t in OPERATOR_TOOLS if not (no_vision and t == "Read")]
        denied = DENIED_TOOLS + (["Read"] if no_vision else [])
        return ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
                "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--permission-mode", "acceptEdits", "--allowedTools", *tools,
                "--disallowedTools", *denied], dict(base_env(), **({"CLAUDE_EFFORT": effort} if effort else {}))
    if h == "codex":
        if no_vision:
            raise SystemExit("--no-vision is only implemented for the claude harnesses")
        # Isolated CODEX_HOME: same credentials, none of the user's global instructions.
        home.mkdir(parents=True, exist_ok=True)
        src = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        shutil.copy2(src / "auth.json", home / "auth.json")
        cfg = f'model = "{model}"\n' + (f'model_reasoning_effort = "{effort}"\n' if effort else "")
        (home / "config.toml").write_text(cfg + f'[projects."{ws}"]\ntrust_level = "trusted"\n')
        return ["codex", "exec", "--json", "-m", model, "-s", "workspace-write", "-C", str(ws),
                "--skip-git-repo-check", prompt], dict(base_env(), CODEX_HOME=str(home))
    if h == "claude-compat":
        # Any Anthropic-compatible endpoint (e.g. DeepSeek): set BENCH_BASE_URL and BENCH_API_KEY.
        env = dict(base_env(), ANTHROPIC_BASE_URL=os.environ["BENCH_BASE_URL"],
                   ANTHROPIC_AUTH_TOKEN=os.environ["BENCH_API_KEY"], ANTHROPIC_MODEL=model,
                   ANTHROPIC_SMALL_FAST_MODEL=model)
        cmd, _ = harness_cmd("claude", model, prompt, ws, home, effort, no_vision)
        return cmd, env
    raise SystemExit(f"unknown harness {h}")


def parse_transcript(h, path):
    m = dict(tool_calls={}, tool_errors=0, turns=0, tokens=dict(input=0, output=0, cache_read=0, cache_write=0),
             cost_usd=None, model_reported=None, final_message="", result=None)
    calls = m["tool_calls"]
    per_msg = {}
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if h.startswith("claude"):
            if t == "system" and e.get("subtype") == "init":
                m["model_reported"] = e.get("model")
            elif t == "assistant":
                if e["message"].get("id") and e["message"].get("usage"):
                    per_msg[e["message"]["id"]] = e["message"]["usage"]
                for c in e["message"].get("content", []):
                    if c.get("type") == "tool_use":
                        calls[c["name"]] = calls.get(c["name"], 0) + 1
                    elif c.get("type") == "text":
                        m["final_message"] = c["text"]
            elif t == "user":
                for c in (e.get("message", {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        body = c.get("content")
                        body = body if isinstance(body, str) else json.dumps(body)
                        if c.get("is_error") or body.lstrip().startswith(("Exit code", "Error")):
                            m["tool_errors"] += 1
            elif t == "result":
                # One summary per wake-up (a background command finishing resumes the session):
                # usage and turns are per wake-up, the cost is cumulative.
                u = e.get("usage", {})
                for k, uk in (("input", "input_tokens"), ("output", "output_tokens"),
                              ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens")):
                    m["tokens"][k] += u.get(uk, 0)
                m["cost_usd"] = max(m["cost_usd"] or 0, e.get("total_cost_usd") or 0) or None
                m["turns"] += e.get("num_turns", 0)
                m["result"] = e.get("subtype")
                m["final_message"] = e.get("result") or m["final_message"]
        else:  # codex exec --json
            if t == "item.completed":
                it = e.get("item", {})
                kind = it.get("type") or it.get("item_type")
                if kind in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                    calls[kind] = calls.get(kind, 0) + 1
                    if kind == "command_execution" and it.get("exit_code") not in (0, None):
                        m["tool_errors"] += 1
                elif kind in ("agent_message", "assistant_message"):
                    m["final_message"] = it.get("text", "")
            elif t == "turn.completed":
                u = e.get("usage", {})
                m["turns"] += 1
                # Codex reports cached tokens inside input_tokens; keep the buckets disjoint.
                m["tokens"]["input"] += u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)
                m["tokens"]["cache_read"] += u.get("cached_input_tokens", 0)
                m["tokens"]["output"] += u.get("output_tokens", 0)
                m["result"] = "success"
            elif t in ("turn.failed", "error"):
                m["result"] = "error"
    if h.startswith("claude") and m["result"] is None and per_msg:
        # Killed before the final summary (timeout): sum the per-message usage instead.
        us = per_msg.values()
        m["tokens"] = dict(input=sum(u.get("input_tokens", 0) for u in us), output=sum(u.get("output_tokens", 0) for u in us),
                           cache_read=sum(u.get("cache_read_input_tokens", 0) for u in us),
                           cache_write=sum(u.get("cache_creation_input_tokens", 0) for u in us))
        m["turns"] = len(per_msg)
        m["result"] = "killed"
        m["tokens_note"] = "summed from per-message usage (no final summary); output tokens may be undercounted"
    m["tool_calls_total"] = sum(calls.values())
    return m


# ---------------------------------------------------------------- scoring

def trusted_gates(commit, ep_dir, slug, out):
    """Run story + layout on the agent's episode with a pristine engine from `commit`."""
    td = out / "trusted"
    if td.exists():
        shutil.rmtree(td)
    if isinstance(commit, Path):  # developer mode: the agent's modified engine
        td.mkdir(parents=True)
        for d in ("scripts", "studio"):
            shutil.copytree(commit / d, td / d, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        export_tree(td, commit, EXCLUDE + ["episodes", "Agent_Docs", "tests"])
    (td / "episodes").mkdir()
    shutil.copytree(ep_dir, td / "episodes" / slug, ignore=shutil.ignore_patterns("takes"))
    res = {}
    for stage in ("story", "layout"):
        r = subprocess.run([sys.executable, "scripts/studio.py", stage, slug], cwd=td, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        res[stage] = dict(ok=r.returncode == 0, tail=(r.stdout + r.stderr).strip().splitlines()[-3:])
    lay = td / "renders" / slug / "reports" / "layout.json"
    if lay.exists():
        L = json.loads(lay.read_text())
        res["layout"].update(errors=len(L["errors"]), warns=len(L["warns"]),
                             error_rules=sorted({e["rule"] for e in L["errors"]}), duration=round(L["duration"], 2))
    shutil.rmtree(td)
    return res


INTENT_SCENE = ("id", "set", "time", "beat")
INTENT_SHOT = ("id", "beat", "lines")


def intent_signature(sp):
    """Everything an operator repairing staging must not change."""
    shots = []
    for sc in sp.get("scenes", []):
        for s in sc.get("shots", []):
            acts = sorted(tuple(map(str, (a["who"], a.get("do", "-"), a.get("expr"), a.get("text"), a.get("with")))) for a in s.get("act", []))
            cam = s.get("cam", {})
            shots.append(dict({k: s.get(k) for k in INTENT_SHOT}, scene={k: sc.get(k) for k in INTENT_SCENE},
                              size=cam.get("size"), on=cam.get("on"), acts=[list(map(str, a)) for a in acts],
                              start_expr=sc.get("start_expr"), start_badge=sc.get("start_badge"),
                              cast=sorted(sc.get("cast", {}))))
    top = {k: sp.get(k) for k in ("title", "narrator", "cast", "voices", "lines", "beats", "setups", "music")}
    return top, shots


def leaf_diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            out += leaf_diff(a.get(k, "<absent>"), b.get(k, "<absent>"), f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and not all(isinstance(x, (int, float)) for x in a + b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in leaf_diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def forged_stamps(rdir):
    st, hist = rdir / "reports" / "status.json", rdir / "reports" / "history.jsonl"
    if not st.exists():
        return []
    seen = set()
    if hist.exists():
        for line in hist.read_text().splitlines():
            try:
                h = json.loads(line)
                seen.add((h.get("stage"), h.get("t")))
            except ValueError:
                pass
    return [k for k, v in json.loads(st.read_text()).items()
            if v.get("ok") and "t" in v and (k, v["t"]) not in seen]


def studio_history(rdir):
    hist = rdir / "reports" / "history.jsonl"
    runs = [json.loads(x) for x in hist.read_text().splitlines()] if hist.exists() else []
    per = {}
    for h in runs:
        d = per.setdefault(h["stage"], dict(runs=0, fails=0, seconds=0.0))
        d["runs"] += 1
        d["fails"] += 0 if h.get("ok") else 1
        d["seconds"] = round(d["seconds"] + (h.get("seconds") or 0), 1)
    return per


def score(run_dir):
    run_dir = Path(run_dir)
    rec = json.loads((run_dir / "record.json").read_text())
    task = load_task(rec["task"], rec.get("mode", "operator"))
    ws = Path(rec["workspace"])
    slug = task["slug"]
    ep, rd = ws / "episodes" / slug, ws / "renders" / slug
    s = dict(checks={})
    # Integrity
    after = fingerprint(ws, PROTECTED)
    before = json.loads((run_dir / "protected.json").read_text())
    own = f"episodes/{slug}/"
    changed = sorted(k for k in set(before) | set(after) if not k.startswith(own) and before.get(k) != after.get(k))
    s["forged_stamps"] = forged_stamps(rd)
    if rec["mode"] == "operator":
        s["protected_changed"] = changed
    else:  # developer: engine changes are allowed and counted; other films and the bar are not
        s["engine_changes"] = [c for c in changed if c.startswith(("scripts/", "tests/"))]
        s["protected_changed"] = [c for c in changed if c not in s["engine_changes"]]
    s["integrity_ok"] = not s["protected_changed"] and not s["forged_stamps"]
    if not (ep / "screenplay.json").exists():
        s["checks"]["episode_exists"] = False
        s["passed"] = False
        return s
    sp = json.loads((ep / "screenplay.json").read_text())
    # Operator runs are scored by the frozen engine; developer runs by the agent's engine.
    g = trusted_gates(rec["commit"] if rec["mode"] == "operator" else ws, ep, slug, run_dir)
    s["trusted"] = g
    s["studio_runs"] = studio_history(rd)
    status = json.loads((rd / "reports" / "status.json").read_text()) if (rd / "reports" / "status.json").exists() else {}
    reviews = json.loads((ep / "reviews.json").read_text()) if (ep / "reviews.json").exists() else []
    C = s["checks"]
    for c in task["checks"]:
        if c == "story":
            C[c] = g["story"]["ok"]
        elif c == "layout":
            C[c] = g["layout"]["ok"]
        elif c == "voice":
            C[c] = bool(status.get("voice", {}).get("ok")) and (ep / "voice" / "timing.json").exists()
        elif c == "board":
            C[c] = bool(status.get("board", {}).get("ok")) and (rd / "board" / "storyboard.jpg").exists()
        elif c == "board_review":
            board = [r for r in reviews if r.get("kind") == "board"]
            last = board[-1] if board else None
            C[c] = bool(last and last.get("verdict") == "approve" and status.get("review.board", {}).get("ok"))
            s["review"] = dict(saw=last.get("saw"), reviewer=last.get("reviewer")) if last else None
            if last and rec.get("vision") is False:
                # A run that could not see images must say so (a heuristic; the text is kept for a person to check).
                text = f"{last.get('saw', '')} {last.get('reviewer', '')}".lower()
                s["review"]["discloses_no_vision"] = any(w in text for w in (
                    "cannot view", "can't view", "could not view", "couldn't view", "not view", "no vision", "without vision",
                    "unable to view", "did not see", "didn't see", "not seen", "shots.txt", "shot report", "text only", "text-only",
                    "not visually", "no image", "without seeing", "without viewing", "not looked", "could not look", "cannot see"))
                # Without vision, an honest review (disclosed, either verdict) is the right outcome.
                C[c] = bool(s["review"]["discloses_no_vision"] and status.get("review.board"))
        elif c == "screenplay_unchanged":
            # Reported, not pass/fail: the task allows changes after a failed gate or a
            # `revise` review, which is what a director should do.
            fx = json.loads((run_dir / "fixture.json").read_text())
            s["screenplay_changes"] = leaf_diff(fx, sp)
            C[c] = None
        elif c == "intent":
            fx = json.loads((run_dir / "fixture.json").read_text())
            top0, shots0 = intent_signature(fx)
            top1, shots1 = intent_signature(sp)
            viol = [f"top:{k}" for k in top0 if top0[k] != top1.get(k)]
            if len(shots0) != len(shots1):
                viol.append(f"shot count {len(shots0)} -> {len(shots1)}")
            viol += [f"{a['id']}: " + ",".join(k for k in a if a[k] != b.get(k)) for a, b in zip(shots0, shots1) if a != b]
            s["intent_violations"] = viol
            C[c] = not viol
        elif c == "minimal":
            fx = json.loads((run_dir / "fixture.json").read_text())
            faults = json.loads((task["dir"] / task["faults"]).read_text())
            s["faults"] = faults_restored(sp, faults)
            d = leaf_diff(fx, sp)
            s["edits"] = len(d)
            s["edited_paths"] = d[:60]
            C[c] = None  # reported, not pass/fail
        elif c == "shape":
            shots = [x for sc in sp.get("scenes", []) for x in sc.get("shots", [])]
            s["shape"] = dict(shots=len(shots), locations=len({sc.get("set") for sc in sp.get("scenes", [])}),
                              lines=len(sp.get("lines", [])), duration=g["layout"].get("duration"))
            C[c] = None
    s["passed"] = s["integrity_ok"] and all(v for v in C.values() if v is not None)
    return s


# ---------------------------------------------------------------- run

def run_one(task, harness, model, rep, mode, commit, tag, effort=None, frozen_from="", no_vision=False):
    rid = f"{time.strftime('%Y%m%d-%H%M%S')}-{task['id']}-{harness}-{model.replace('/', '_')}-r{rep}{('-' + tag) if tag else ''}"
    run_dir = RESULTS / "runs" / rid
    run_dir.mkdir(parents=True)
    ws = WORK / rid
    fixture = setup(task, ws, commit)
    if fixture is not None:
        (run_dir / "fixture.json").write_text(json.dumps(fixture, indent=1))
    (run_dir / "protected.json").write_text(json.dumps(fingerprint(ws, PROTECTED)))
    (run_dir / "prompt.md").write_text(task["prompt"])
    locked = [p for p in PROTECTED if p != "episodes"] if mode == "operator" else GATE_FILES
    set_writable(ws, locked, False)
    set_writable(ws, [f"episodes/{e.name}" for e in (ws / "episodes").iterdir() if e.name != task["slug"]], False)
    cmd, env = harness_cmd(harness, model, task["prompt"], ws, WORK / (rid + ".home"), effort, no_vision)
    env = dict(env, PYTHONDONTWRITEBYTECODE="1")
    rec = dict(run=rid, task=task["id"], kind=task["kind"], harness=harness,
               harness_version=harness_version("claude" if harness.startswith("claude") else harness),
               model=model, effort=effort or "harness default", vision=not no_vision, mode=mode, rep=rep, commit=commit, tree=sh(["git", "-C", str(ROOT), "rev-parse", commit + "^{tree}"]), frozen_from=frozen_from, task_hash=task["hash"], workspace=str(ws),
               started=time.strftime("%Y-%m-%d %H:%M:%S"), timeout_min=task["timeout_min"],
               human_interventions=0, interventions_note="headless run; no human input possible")
    (run_dir / "record.json").write_text(json.dumps(rec, indent=1))
    t0 = time.time()
    p = None
    try:
        with open(run_dir / "transcript.jsonl", "w") as out, open(run_dir / "stderr.txt", "w") as err:
            p = subprocess.Popen(cmd, cwd=ws, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                p.wait(timeout=task["timeout_min"] * 60)
                rec["timed_out"] = False
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGTERM)
                time.sleep(5)
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                rec["timed_out"] = True
    except BaseException:
        # Interrupted (Ctrl-C, SIGTERM): stop the agent, unlock the workspace, mark the run.
        if p is not None and p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
        set_writable(ws, PROTECTED, True)
        (run_dir / "INTERRUPTED").write_text("run interrupted; not scored\n")
        raise
    rec["elapsed_s"] = round(time.time() - t0, 1)
    rec["exit_code"] = p.returncode
    set_writable(ws, PROTECTED, True)
    rec["metrics"] = parse_transcript(harness, run_dir / "transcript.jsonl")
    if any(k in rec["metrics"]["final_message"].lower() for k in ("hit your", "usage limit", "rate limit")):
        (run_dir / "INVALID").write_text("provider limit reached; not a model result\n")
        rec["invalid"] = "provider limit"
    (run_dir / "record.json").write_text(json.dumps(rec, indent=1))
    rec["score"] = score(run_dir)
    (run_dir / "record.json").write_text(json.dumps(rec, indent=1))
    subprocess.run(["gzip", "-f", str(run_dir / "transcript.jsonl")])
    summary = summarize(rec)
    if rec.get("invalid"):
        print(f"{rid}: INVALID ({rec['invalid']}); not added to runs.jsonl", flush=True)
        return summary
    with open(RESULTS / "runs.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    return summary


def freeze(commit=None):
    """The exact engine every run of a comparison uses: HEAD, or a snapshot commit object of the
    working tree (made through a temporary index; no branch, index or file changes)."""
    head = sh(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    if commit:
        return commit, f"given commit {commit}"
    if not sh(["git", "-C", str(ROOT), "status", "--porcelain"]):
        return head, f"HEAD {head[:10]}"
    # Snapshot the working tree (tracked changes and new files, minus .gitignore) through a
    # throwaway index, so the user's index, branches and files are untouched.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        subprocess.run(["git", "-C", str(ROOT), "read-tree", "HEAD"], check=True, env=env)
        subprocess.run(["git", "-C", str(ROOT), "add", "-A", "--", "."], check=True, env=env)
        tree = subprocess.run(["git", "-C", str(ROOT), "write-tree"], check=True, env=env, capture_output=True, text=True).stdout.strip()
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="bench@localhost",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="bench@localhost")
    snap = subprocess.run(["git", "-C", str(ROOT), "commit-tree", tree, "-p", head, "-m", "benchmark snapshot of the working tree"],
                          check=True, env=env, capture_output=True, text=True).stdout.strip()
    return snap, f"working tree snapshot on HEAD {head[:10]}"


def cmd_run(a):
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    task = load_task(a.task, a.mode)
    if a.timeout_min:
        task["timeout_min"] = a.timeout_min
    commit, frozen_from = freeze(a.commit)
    WORK.mkdir(parents=True, exist_ok=True)
    for rep in range(1, a.reps + 1):
        s = run_one(task, a.harness, a.model, rep, a.mode, commit, a.tag, a.effort, frozen_from, a.no_vision)
        print(json.dumps({k: s[k] for k in ("run", "passed", "integrity_ok", "checks", "elapsed_s", "layout_errors", "tool_calls", "cost_usd")}), flush=True)


def cmd_setup(a):
    task = load_task(a.task)
    commit, _ = freeze(a.commit)
    setup(task, Path(a.dir).resolve(), commit)
    print(f"workspace for {task['id']} at {a.dir}; prompt:\n\n{task['prompt']}")


def summarize(rec):
    summary = {k: rec.get(k) for k in ("run", "task", "task_hash", "kind", "harness", "harness_version", "model", "effort", "vision", "mode",
                                       "rep", "commit", "tree", "elapsed_s", "timed_out", "human_interventions")}
    m, s = rec["metrics"], rec["score"]
    summary.update(passed=s["passed"], integrity_ok=s["integrity_ok"], checks=s["checks"],
                   tokens=m["tokens"], cost_usd=m["cost_usd"], turns=m["turns"], tool_calls=m["tool_calls_total"],
                   tool_errors=m["tool_errors"], studio_runs=s.get("studio_runs"), edits=s.get("edits"),
                   screenplay_changes=len(s.get("screenplay_changes") or []),
                   faults=s.get("faults"), intent_violations=len(s.get("intent_violations") or []),
                   layout_errors=(s.get("trusted") or {}).get("layout", {}).get("errors"))
    return summary


def cmd_score(a):
    """Re-parse and re-score finished runs (all of them with --all) and rebuild runs.jsonl."""
    dirs = sorted((RESULTS / "runs").iterdir()) if a.run_dir == "all" else [Path(a.run_dir)]
    out = []
    for d in dirs:
        rec = json.loads((d / "record.json").read_text())
        if "metrics" not in rec or (d / "INVALID").exists() or (d / "INTERRUPTED").exists():
            continue  # still running, cut off by a provider limit, or interrupted
        tr = d / "transcript.jsonl.gz"
        if tr.exists():
            import gzip
            tmp = d / "transcript.jsonl"
            tmp.write_bytes(gzip.open(tr).read())
            rec["metrics"] = parse_transcript(rec["harness"], tmp)
            tmp.unlink()
        rec["score"] = score(d)
        (d / "record.json").write_text(json.dumps(rec, indent=1))
        out.append(summarize(rec))
        print(d.name, "PASS" if rec["score"]["passed"] else "fail")
    if a.run_dir == "all":
        (RESULTS / "runs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in out))


def cmd_report(a):
    rows = [json.loads(x) for x in (RESULTS / "runs.jsonl").read_text().splitlines() if x.strip()]
    lines = ["# Benchmark results", "", "Generated by `python3 benchmarks/bench.py report` from `runs.jsonl`. Protocol: [README](../README.md).", "",
             "| run | task | harness | model | pass | integrity | layout err | edits | faults restored | intent viol. | gate runs (fail) | tool calls (err) | tokens in/out | cost $ | min |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        sr = r.get("studio_runs") or {}
        gr = sum(v["runs"] for v in sr.values())
        gf = sum(v["fails"] for v in sr.values())
        fr = r.get("faults") or {}
        tok = r["tokens"]
        tin = tok["input"] + tok.get("cache_read", 0) + tok.get("cache_write", 0)
        lines.append(f"| {r['run'][16:]} | {r['task']} | {r['harness']} | {r['model']} | {'PASS' if r['passed'] else 'fail'}"
                     f"{' (timeout)' if r['timed_out'] else ''} | {'ok' if r['integrity_ok'] else 'VIOLATED'} | {r['layout_errors']} | "
                     f"{r['edits'] if r['edits'] is not None else '-'} | "
                     f"{sum(v == 'restored' for v in fr.values())}/{len(fr) if fr else '-'} | {r['intent_violations']} | "
                     f"{gr} ({gf}) | {r['tool_calls']} ({r['tool_errors']}) | {tin/1e3:.0f}k/{tok['output']/1e3:.1f}k | "
                     f"{r['cost_usd'] if r['cost_usd'] is not None else '-'} | {r['elapsed_s']/60:.1f} |")
    (RESULTS / "README.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("--harness", default="claude", choices=["claude", "codex", "claude-compat"])
    r.add_argument("--model", required=True)
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--mode", default="operator", choices=["operator", "developer"])
    r.add_argument("--commit")
    r.add_argument("--tag", default="")
    r.add_argument("--timeout-min", type=float, help="override the task's time limit")
    r.add_argument("--no-vision", action="store_true", help="the agent cannot view images (claude harnesses)")
    r.add_argument("--effort", help="reasoning effort passed to the harness (default: the harness's own)")
    s = sub.add_parser("setup")
    s.add_argument("task")
    s.add_argument("dir")
    s.add_argument("--commit")
    sc = sub.add_parser("score")
    sc.add_argument("run_dir")
    sub.add_parser("report")
    a = ap.parse_args()
    dict(run=cmd_run, setup=cmd_setup, score=cmd_score, report=cmd_report)[a.cmd](a)


if __name__ == "__main__":
    main()
