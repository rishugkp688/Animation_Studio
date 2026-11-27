#!/usr/bin/env python3
"""Blocky Studio: one command per department, each with a gate.

    python3 scripts/studio.py new     SLUG [--title "..."]   scaffold an episode (screenplay, notes, retrospective)
    python3 scripts/studio.py story   SLUG                   story editor: structure lint
    python3 scripts/studio.py voice   SLUG                   casting/recording: offline TTS + word alignment
    python3 scripts/studio.py layout  SLUG                   layout + animation QC (collisions, feet, cameras)
    python3 scripts/studio.py board   SLUG [--animatic]      storyboard sheet (+ fast animatic with scratch sound)
    python3 scripts/studio.py review  SLUG board   --verdict approve|revise --saw "..."
    python3 scripts/studio.py render  SLUG --preset preview|final [--frames A-B] [--unreviewed "why"]
    python3 scripts/studio.py sound   SLUG --preset ...      ambience, foley, score, mix (needs a build)
    python3 scripts/studio.py deliver SLUG --preset ...      captions, encode, technical + pacing QC, sheets
    python3 scripts/studio.py review  SLUG picture --preset ... --verdict ... --saw "..." [--note CODE="why" ...]
    python3 scripts/studio.py make    SLUG --preset preview  every stage in order; stops at the first failed gate
                                                             and at the storyboard review checkpoint
    python3 scripts/studio.py status  SLUG                   what has been done, what is stale and why
    python3 scripts/studio.py retro   SLUG                   refresh the measured part of the retrospective

Generated files go to renders/SLUG/<preset>/. Reports go to renders/SLUG/reports/.
Reviews (human/agent judgement) go to episodes/SLUG/reviews.json; the engine records them and
checks they match the current pictures, but never makes the judgement itself.
No stage calls a language model; re-rendering costs no credits.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kit import fonts  # noqa: E402

AUDIO_PY = ROOT / ".audio-tools/venv/bin/python"
ASR_PY = ROOT / ".audio-tools/asr/bin/python"
BLENDER = shutil.which("blender") or "blender"
KIT = ROOT / "scripts/kit"
QUALITY_PATH = ROOT / "studio/quality-bar.json"
QUALITY = json.loads(QUALITY_PATH.read_text())
# Where episodes and renders live; tests point these at a temporary folder.
EPISODES = ROOT / "episodes"
RENDERS = ROOT / "renders"
MIN_REVIEW_WORDS = 12


def ep_dir(slug):
    return EPISODES / slug


def out_dir(slug, preset):
    return RENDERS / slug / preset


def reports(slug):
    d = RENDERS / slug / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(slug):
    from kit.layout import load_screenplay, load_timing
    e = ep_dir(slug)
    return load_screenplay(e / "screenplay.json"), load_timing(e)


def clean_env():
    """Environment for Blender: drop pyenv/venv overrides so Blender's embedded Python uses the
    system interpreter it was built against (with its own site-packages, e.g. Pillow)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYENV", "_PYENV", "PYTHON", "VIRTUAL_ENV"))}
    env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if ".pyenv" not in p and "/pyenv/" not in p)
    # Headless builds never play sound. Without an audio device (agent sandboxes, containers, CI)
    # Blender's OpenAL backend blocks on exit, so give it the null driver.
    env.setdefault("ALSOFT_DRIVERS", "null")
    return env


def run(cmd, **kw):
    print("$", " ".join(map(str, cmd)), flush=True)
    if str(cmd[0]) == BLENDER:
        kw.setdefault("env", clean_env())
    return subprocess.run(list(map(str, cmd)), **kw)


# ---------------------------------------------------------------- fingerprints
def fingerprint(*paths):
    """Content hash of files and folders (folders recursively, ignoring Python caches).
    A missing path hashes as its name, so 'absent' and 'present' never collide."""
    h = hashlib.sha1()
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    h.update(str(f.relative_to(p)).encode())
                    h.update(f.read_bytes())
        elif p.exists():
            h.update(p.read_bytes())
        else:
            h.update(b"<missing>" + p.name.encode())
    return h.hexdigest()[:12]


def json_fingerprint(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]


def tool_fingerprint(exe):
    """Which binary, and which build of it (path, size, mtime): an upgrade makes renders stale."""
    p = shutil.which(str(exe))
    if not p:
        return "<missing>"
    s = Path(p).resolve().stat()
    return json_fingerprint([str(Path(p).resolve()), s.st_size, s.st_mtime_ns])


def venv_fingerprint(venv):
    """Installed packages and versions of a venv (its *.dist-info folder names)."""
    names = sorted(d.name for d in Path(venv).glob("lib/python*/site-packages/*.dist-info"))
    return json_fingerprint(names) if names else "<missing>"


def fonts_fingerprint():
    found = [fonts.find(w)[0] for w in ("extrabold", "bold", "regular")]
    return json_fingerprint([(f, Path(f).stat().st_size if f else None) for f in found])


def split_stage(stage):
    """'render.preview' -> ('render', 'preview'); 'review.board' -> ('review.board', None);
    'review.picture.final' -> ('review.picture', 'final')."""
    if stage.startswith("review."):
        parts = stage.split(".")
        return ".".join(parts[:2]), (parts[2] if len(parts) > 2 else None)
    kind, _, preset = stage.partition(".")
    return kind, preset or None


# The kit modules each stage actually runs (imports followed by hand; tests/test_studio.py
# checks every kit module is listed somewhere). A change elsewhere in the kit does not make
# the stage stale, so editing the story gate never asks for a 75-minute re-render.
_LAYOUT = ["__init__.py", "mathx.py", "rig.py", "acting.py", "sets.py", "geom.py", "camera.py", "layout.py"]
KIT_FILES = {
    "story": _LAYOUT + ["story.py", "catalog.py"],
    "voice": ["voice.py", "align.py"],
    "layout": _LAYOUT + ["qc.py", "shotreport.py"],
    "render": _LAYOUT + ["faces.py", "fonts.py", "blender_build.py"],
    "sound": ["sound.py", "listen.py"],
    "deliver": ["captions.py", "fonts.py"],
    # Not a stage: `repair` proposes screenplay edits, which the layout gate then validates.
    "repair": _LAYOUT + ["qc.py", "repair.py"],
}


# Which stages' outputs each stage consumes. Staleness propagates down these edges.
UPSTREAM = {
    "story": [], "voice": [], "layout": ["story", "voice"], "board": ["layout"], "review.board": ["board"],
    "render": ["layout", "review.board"], "sound": ["render.{p}", "voice"], "deliver": ["render.{p}", "sound.{p}"],
    "review.picture": ["deliver.{p}"],
}


def stage_inputs(slug, stage):
    """Everything a stage's result depends on, as {name: fingerprint}.

    engine.*  code that computes the result     config.*  production configuration
    assets.*  fonts and recorded takes            tools.*   external programs and venvs
    and the upstream artifacts the stage reads (timing, plan, frames, mix, sheets)."""
    kind, preset = split_stage(stage)
    e = ep_dir(slug)
    sp_path = e / "screenplay.json"
    group = {"story": "story", "voice": "voice", "layout": "layout", "board": "render", "render": "render",
             "sound": "sound", "deliver": "deliver"}.get(kind)
    eng = {"engine.kit": fingerprint(*[KIT / f for f in KIT_FILES[group]])} if group else {}
    if kind in ("story", "layout", "board", "deliver"):     # stages whose logic lives in studio.py
        eng["engine.studio"] = fingerprint(Path(__file__))
    timing = {"voice.timing": fingerprint(e / "voice/timing.json")}
    if kind == "story":
        return {"screenplay": fingerprint(sp_path), **timing, "config.quality_bar": fingerprint(QUALITY_PATH), **eng}
    if kind == "voice":
        # Only what casting reads: a camera change must not ask for new takes.
        sp = json.loads(sp_path.read_text()) if sp_path.exists() else {}
        cast = {k: sp.get(k) for k in ("lines", "voices", "narrator")}
        return {"screenplay.lines_and_voices": json_fingerprint(cast), "tools.tts": venv_fingerprint(ROOT / ".audio-tools/venv"), **eng}
    if kind == "layout":
        return {"screenplay": fingerprint(sp_path), **timing, **eng}
    if kind in ("board", "render"):
        return {"screenplay": fingerprint(sp_path), **timing, "assets.fonts": fonts_fingerprint(),
                "tools.blender": tool_fingerprint(BLENDER), **eng}
    if kind == "review.board":
        return {"board.sheet": fingerprint(out_dir(slug, "board") / "storyboard.jpg")}
    out = out_dir(slug, preset)
    if kind == "sound":
        return {"screenplay": fingerprint(sp_path), "render.plan": fingerprint(out / "plan.json"),
                "assets.voice_takes": fingerprint(e / "voice"), "tools.audio": venv_fingerprint(ROOT / ".audio-tools/venv"),
                "tools.asr": venv_fingerprint(ROOT / ".audio-tools/asr"), **eng}
    if kind == "deliver":
        return {"screenplay": fingerprint(sp_path), "render.plan": fingerprint(out / "plan.json"),
                "render.frames": fingerprint(out / "frames/manifest.json"), "sound.mix": fingerprint(out / "sound/mix.wav"),
                "config.quality_bar": fingerprint(QUALITY_PATH), "engine.audit": fingerprint(ROOT / "scripts/video_audit.py"),
                "assets.fonts": fonts_fingerprint(), "tools.ffmpeg": tool_fingerprint("ffmpeg"), **eng}
    if kind == "review.picture":
        return {"deliver.movie": fingerprint(out / f"{slug}-{preset}.mp4"), "deliver.sheet": fingerprint(out / "frames.jpg")}
    raise ValueError(stage)


def load_status(slug):
    p = reports(slug) / "status.json"
    return json.loads(p.read_text()) if p.exists() else {}


def changed_inputs(slug, stage, rec):
    """Names of inputs that differ from when the stage last ran (None: no record of inputs)."""
    if "inputs" not in rec:
        return None
    now = stage_inputs(slug, stage)
    return sorted(k for k in set(now) | set(rec["inputs"]) if now.get(k) != rec["inputs"].get(k))


def freshness(slug, stage, st=None, _memo=None):
    """(ok, reasons): ok when the stage passed, its inputs are unchanged and everything upstream is ok."""
    st = load_status(slug) if st is None else st
    _memo = {} if _memo is None else _memo
    if stage in _memo:
        return _memo[stage]
    kind, preset = split_stage(stage)
    rec = st.get(stage)
    reasons = []
    if rec is None:
        reasons.append("not run")
    else:
        if not rec["ok"]:
            reasons.append("failed")
        ch = changed_inputs(slug, stage, rec)
        if ch is None:
            reasons.append("no input record (ran before input tracking); re-run")
        elif ch:
            reasons.append("changed: " + ", ".join(ch))
    for up in UPSTREAM[kind]:
        up = up.format(p=preset)
        ok, _ = freshness(slug, up, st, _memo)
        if not ok:
            reasons.append(f"upstream {up} not current")
    _memo[stage] = (not reasons, reasons)
    return _memo[stage]


# ---------------------------------------------------------------- stamps and history
T0 = [time.time()]


def begin():
    T0[0] = time.time()


def history(slug, event):
    with open(reports(slug) / "history.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")


def stamp(slug, stage, ok, data):
    p = reports(slug) / "status.json"
    st = load_status(slug)
    rec = dict(ok=ok, at=time.strftime("%Y-%m-%d %H:%M:%S"), t=round(time.time(), 1),
               seconds=round(time.time() - T0[0], 1), inputs=stage_inputs(slug, stage), **data)
    st[stage] = rec
    p.write_text(json.dumps(st, indent=1))
    # Keep counts in the history, drop the long lists (they stay in the stage's own report).
    history(slug, dict(stage=stage, **{k: v for k, v in rec.items()
                                       if not (k in ("errors", "warns", "cameras") and isinstance(v, (list, dict)))}))


def manual(slug, what, why):
    """Record a manual intervention (an override of a gate or checkpoint) for the retrospective."""
    history(slug, dict(stage="manual", what=what, why=why, at=time.strftime("%Y-%m-%d %H:%M:%S"), t=round(time.time(), 1)))


def gate(ok, stage, msg, code=2):
    print(("PASS " if ok else "FAIL ") + f"[{stage}] {msg}", flush=True)
    if not ok:
        sys.exit(code)


# ---------------------------------------------------------------- frames
FRAME_MANIFEST = "manifest.json"


def frame_files(out):
    return sorted((out / "frames").glob("f_*.png"))


def predicted_plan_hash(slug):
    """The fingerprint plan.json will have after the next build (same compile, same bytes as
    blender_build.py writes). Used to keep frames that are already rendered from that plan."""
    from kit.layout import compile_plan
    sp, timing = load(slug)
    return hashlib.sha1(json.dumps(compile_plan(sp, timing).summary(), indent=1).encode()).hexdigest()[:12]


def prune_frames(out, keep_plan=None, frame_range=None):
    """Delete frames that must be rendered again; return (kept, removed).

    Blender skips frames that exist (use_overwrite=False) and leaves an empty placeholder for a
    frame interrupted mid-render, so before rendering:
    - keep_plan=None, no range: delete everything (a clean full render);
    - keep_plan=HASH: keep only frames recorded as rendered from that plan and unchanged since
      (an interrupted render resumes); delete stale, empty and unrecorded frames;
    - frame_range=(a, b): delete exactly those frames so a partial render really re-renders them."""
    d = out / "frames"
    mp = d / FRAME_MANIFEST
    man = json.loads(mp.read_text()) if mp.exists() else {"frames": {}}
    kept, removed = 0, 0
    for f in frame_files(out):
        k = f.stem[2:]
        m = man["frames"].get(k)
        s = f.stat()
        if frame_range:
            drop = k.isdigit() and frame_range[0] <= int(k) <= frame_range[1] or s.st_size == 0
        else:
            drop = (keep_plan is None or not m or m["plan"] != keep_plan or s.st_size == 0
                    or (s.st_size, s.st_mtime_ns) != (m["size"], m["mtime_ns"]))
        if drop:
            f.unlink()
            man["frames"].pop(k, None)
            removed += 1
        else:
            kept += 1
    if mp.exists() or man["frames"]:
        if keep_plan is None and not frame_range:
            mp.unlink(missing_ok=True)
        else:
            mp.write_text(json.dumps(man, indent=0))
    return kept, removed


def _entry(f, plan_hash):
    s = f.stat()
    return dict(plan=plan_hash, size=s.st_size, mtime_ns=s.st_mtime_ns)


def record_frames(out, since):
    """After a render: every frame written since `since` (epoch s) was made from the current plan."""
    d = out / "frames"
    mp = d / FRAME_MANIFEST
    man = json.loads(mp.read_text()) if mp.exists() else {"frames": {}}
    h = fingerprint(out / "plan.json")
    for f in frame_files(out):
        if f.stat().st_mtime >= since:
            man["frames"][f.stem[2:]] = _entry(f, h)
    man["plan"] = h
    mp.write_text(json.dumps(man, indent=0))
    return man


def adopt_legacy_frames(out):
    """Frames rendered before the manifest existed: adopt them only if every one is newer than
    plan.json (one complete render after the last build). Returns True if adopted."""
    d = out / "frames"
    if (d / FRAME_MANIFEST).exists() or not (out / "plan.json").exists():
        return False
    files = frame_files(out)
    built = (out / "plan.json").stat().st_mtime
    if not files or any(f.stat().st_mtime < built for f in files):
        return False
    h = fingerprint(out / "plan.json")
    (d / FRAME_MANIFEST).write_text(json.dumps({"plan": h, "adopted": True, "frames": {f.stem[2:]: _entry(f, h) for f in files}}, indent=0))
    return True


def check_frames(out, n_frames):
    """Problems with the rendered frames for delivery: missing, extra, unrecorded, rendered from
    another plan, changed since rendering, or empty. [] means every frame 1..n is current."""
    adopt_legacy_frames(out)
    mp = out / "frames" / FRAME_MANIFEST
    man = json.loads(mp.read_text()) if mp.exists() else {"frames": {}}
    h = fingerprint(out / "plan.json")
    have = {f.stem[2:]: f for f in frame_files(out)}
    want = {f"{i:04d}" for i in range(1, n_frames + 1)}
    problems = []

    def add(label, keys):
        keys = sorted(keys)
        if keys:
            problems.append(f"{len(keys)} {label}: {', '.join(keys[:6])}{' ...' if len(keys) > 6 else ''}")

    add("missing", want - set(have))
    add("extra (not in this plan)", set(have) - want)
    present = sorted(want & set(have))
    add("unrecorded (no render manifest entry)", [k for k in present if k not in man["frames"]])
    add("rendered from an older plan", [k for k in present if k in man["frames"] and man["frames"][k]["plan"] != h])
    changed, empty = [], []
    for k in present:
        m = man["frames"].get(k)
        if not m:
            continue
        s = have[k].stat()
        if s.st_size == 0:
            empty.append(k)
        elif (s.st_size, s.st_mtime_ns) != (m["size"], m["mtime_ns"]):
            changed.append(k)
    add("empty", empty)
    add("changed after rendering", changed)
    return problems


# ---------------------------------------------------------------- reviews (external judgement, recorded)
def reviews_path(slug):
    return ep_dir(slug) / "reviews.json"


def load_reviews(slug):
    p = reviews_path(slug)
    return json.loads(p.read_text()) if p.exists() else []


def latest_review(slug, kind, preset=None):
    for r in reversed(load_reviews(slug)):
        if r["kind"] == kind and r.get("preset") == preset:
            return r
    return None


def review_artifacts(slug, kind, preset=None):
    if kind == "board":
        return {"storyboard.jpg": out_dir(slug, "board") / "storyboard.jpg"}
    out = out_dir(slug, preset)
    return {f"{slug}-{preset}.mp4": out / f"{slug}-{preset}.mp4", "frames.jpg": out / "frames.jpg"}


def review_status(slug, kind, preset=None):
    """(ok, why). ok when the latest review approves exactly the pictures that exist now."""
    r = latest_review(slug, kind, preset)
    if r is None:
        return False, "no review recorded"
    if r["verdict"] != "approve":
        return False, f"latest review says '{r['verdict']}' ({r['at']})"
    for name, p in review_artifacts(slug, kind, preset).items():
        if r["artifacts"].get(name) != fingerprint(p):
            return False, f"{name} changed after the review ({r['at']})"
    return True, f"approved {r['at']} by {r['reviewer']}"


def pacing_notes(slug, preset):
    p = reports(slug) / f"pacing.{preset}.json"
    return json.loads(p.read_text()).get("artistic", []) if p.exists() else []


def layout_current(slug):
    """(ok, why): has the layout gate passed on the current screenplay, timing and engine?"""
    rec = load_status(slug).get("layout")
    if not rec:
        return False, "the layout gate has not run"
    if not rec.get("ok"):
        return False, f"the last layout gate failed ({(rec.get('data') or {}).get('errors', '?')} errors)"
    ok, reasons = freshness(slug, "layout")
    return (True, "") if ok else (False, "the layout gate is out of date: " + "; ".join(reasons))


def cmd_review(a):
    """Record a visual review. The reviewer looks at the pictures; this only writes it down."""
    begin()
    kind, preset = a.kind, (a.preset if a.kind == "picture" else None)
    arts = review_artifacts(a.slug, kind, preset)
    for name, p in arts.items():
        if not p.exists():
            sys.exit(f"{p} does not exist; run `studio.py {'board' if kind == 'board' else 'deliver'}` first")
    if kind == "board" and a.verdict == "approve":
        ok, why = layout_current(a.slug)
        if not ok:
            sys.exit(f"cannot approve the storyboard: {why}. The sheet may show staging that fails QC; "
                     f"run `studio.py layout {a.slug}` (and `board` again if the screenplay changed), then review.")
    if kind == "picture":
        rec = load_status(a.slug).get(f"deliver.{preset}")
        if not rec or not rec["ok"]:
            sys.exit(f"deliver.{preset} has not passed; review the picture after delivery QC passes")
    if len((a.saw or "").split()) < MIN_REVIEW_WORDS:
        sys.exit(f"--saw must describe what you saw in at least {MIN_REVIEW_WORDS} words (shot ids, seconds, what reads and what does not)")
    notes = {}
    for n in a.note or []:
        code, _, text = n.partition("=")
        notes[code.strip()] = text.strip()
    required = [f["code"] for f in pacing_notes(a.slug, preset)] if kind == "picture" else []
    missing = [c for c in required if len(notes.get(c, "").split()) < 3]
    if missing and a.verdict == "approve":
        sys.exit("approval needs a written disposition for each artistic note: " + ", ".join(f'--note {c}="why it is right / what was done"' for c in missing))
    rec = dict(kind=kind, preset=preset, verdict=a.verdict, reviewer=a.reviewer, at=time.strftime("%Y-%m-%d %H:%M:%S"),
               saw=a.saw.strip(), notes=notes, required=required,
               artifacts={name: fingerprint(p) for name, p in arts.items()})
    allr = load_reviews(a.slug)
    allr.append(rec)
    reviews_path(a.slug).write_text(json.dumps(allr, indent=1) + "\n")
    stage = "review.board" if kind == "board" else f"review.picture.{preset}"
    stamp(a.slug, stage, a.verdict == "approve", dict(verdict=a.verdict))
    gate(a.verdict == "approve", stage, f"{a.verdict}: recorded in {reviews_path(a.slug)}")


# ---------------------------------------------------------------- stages
def cmd_new(a):
    d = ep_dir(a.slug)
    if d.exists():
        sys.exit(f"{d} already exists")
    tpl = json.loads((ROOT / "studio/templates/screenplay.json").read_text())
    tpl["title"] = a.title or a.slug.replace("-", " ").title()
    d.mkdir(parents=True)
    (d / "screenplay.json").write_text(json.dumps(tpl, indent=1))
    (d / "notes.md").write_text(f"# {tpl['title']}\n\nDirector's notes, review decisions and open issues go here.\n")
    retro = (ROOT / "studio/templates/retrospective.md").read_text().replace("TITLE", tpl["title"]).replace("SLUG", a.slug)
    (d / "retrospective.md").write_text(retro.replace("STARTED", time.strftime("%Y-%m-%d")))
    print(f"Created {d}/screenplay.json, notes.md and retrospective.md. Next: write it with "
          f"studio/departments/01-story.md, then `studio.py story {a.slug}`.")


def cmd_story(a):
    begin()
    from kit.story import lint
    from kit.layout import compile_plan
    from kit import catalog
    sp, timing = load(a.slug)
    plan = None
    bad = catalog.validate(sp, catalog.schema())
    if bad:  # structure and vocabulary first: a precise path beats a compile traceback
        r = dict(errors=[f"schema: {e}" for e in bad], warns=[], facts={})
    else:
        try:
            plan = compile_plan(sp, timing, solve_cameras=False)
        except Exception as e:  # compile problems are story problems at this stage
            r = dict(errors=[f"compile: {type(e).__name__}: {e}"], warns=[], facts={})
        else:
            r = lint(sp, timing, plan, QUALITY)
    (reports(a.slug) / "story.json").write_text(json.dumps(r, indent=1))
    for w in r["warns"]:
        print("warn:", w)
    for e in r["errors"]:
        print("ERROR:", e)
    print(json.dumps(r["facts"]))
    stamp(a.slug, "story", not r["errors"], dict(facts=r["facts"], n_errors=len(r["errors"]), n_warns=len(r["warns"])))
    gate(not r["errors"], "story", f"{len(r['errors'])} errors, {len(r['warns'])} warnings{' (scratch timing)' if plan and plan.scratch_timing else ''}")


def cmd_voice(a):
    begin()
    r = run([AUDIO_PY, KIT / "voice.py", ep_dir(a.slug)] + (["--force", *a.force] if a.force else []))
    timing = json.loads((ep_dir(a.slug) / "voice/timing.json").read_text())
    bad = [k for k, v in timing["lines"].items() if v.get("match", 1) < 0.8]
    stamp(a.slug, "voice", r.returncode == 0 and not bad, dict(lines=len(timing["lines"]), unclear=bad))
    gate(r.returncode == 0 and not bad, "voice", f"{len(timing['lines'])} takes; unclear: {bad or 'none'}")


def cmd_layout(a):
    begin()
    from kit.layout import compile_plan
    from kit import qc
    sp, timing = load(a.slug)
    if timing is None:
        print("note: no voice timing yet; using scratch estimates")
    plan = compile_plan(sp, timing)
    rep = qc.run(plan, step=1 if not a.quick else 2)
    merged = rep.merged()
    errors = [i for i in merged if i["severity"] == "error"]
    out = dict(duration=plan.duration, shots=len(plan.shots), errors=errors, warns=[i for i in merged if i["severity"] != "error"],
               cameras={s["id"]: dict(size=s["camera"]["size"], side=s["camera"]["side"], report=s["camera"]["report"], hide=s["camera"]["hide"]) for s in plan.shots})
    (reports(a.slug) / "layout.json").write_text(json.dumps(out, indent=1))
    if not a.quick:
        from kit import shotreport
        shots = shotreport.describe(plan, sp)
        (reports(a.slug) / "shots.json").write_text(json.dumps(shots, indent=1))
        (reports(a.slug) / "shots.txt").write_text(shotreport.to_text(shots, a.slug))
        flagged = sum(1 for x in shots if x["flags"])
        print(f"shot report: {reports(a.slug) / 'shots.txt'} ({flagged} of {len(shots)} shots flagged for the storyboard review)")
    for i in merged:
        print(f"{i['severity'][:4]:4} {i['rule']:16} {i['shot'] or '-':5} {i['t0']:6.2f}-{i['t1']:6.2f}s  {i['msg']}" + (f"  (depth {i['depth']} m)" if i.get("depth") else ""))
    msg = f"{len(errors)} errors, {len(out['warns'])} warnings over {int(plan.duration * plan.fps)} frames"
    if a.quick:
        # Every 2nd frame and no foot-slide check: useful while restaging, never recorded as the gate.
        gate(not errors, "layout --quick", msg + " (not recorded; run without --quick to pass the layout gate)")
        return
    stamp(a.slug, "layout", not errors, dict(errors=len(errors), error_rules=sorted({i["rule"] for i in errors}), warns=len(out["warns"])))
    gate(not errors, "layout", msg)


def blender_build(slug, preset, extra):
    out = out_dir(slug, preset)
    cmd = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P", KIT / "blender_build.py", "--", ep_dir(slug), "--preset", preset, "--out", out] + extra
    r = run(cmd)
    if r.returncode:
        sys.exit("blender build failed")
    return out


def cmd_board(a):
    begin()
    from kit.layout import compile_plan
    sp, timing = load(a.slug)
    plan = compile_plan(sp, timing, solve_cameras=False)
    # One key frame per shot, at the shot's key moment.
    frames = []
    for sh in plan.shots:
        key = sh["cam"].get("key", 0.6)
        frames.append(plan.frame(sh["t0"] + (sh["t1"] - sh["t0"]) * key))
    out = blender_build(a.slug, "board", ["--stills", ",".join(map(str, frames))] + (["--render", "--fast-bake"] if a.animatic else []))
    stills = out / "stills"
    lines = {l["id"]: l["text"] for l in sp["lines"]}
    bold, _ = fonts.require("bold")
    regular, _ = fonts.require("regular")
    # Labelled contact sheet with PIL (via Blender's Python, which ships Pillow).
    script = f"""
import json
from PIL import Image, ImageDraw, ImageFont
shots = {json.dumps([dict(id=s['id'], size=s['cam'].get('size'), beat=s.get('beat'), text=' / '.join(lines.get(e if isinstance(e, str) else e['id'], '') for e in s['spec'].get('lines', [])), f=f) for s, f in zip(plan.shots, frames)])}
W, H = 270, 480
cols = 6
rows = (len(shots) + cols - 1) // cols
sheet = Image.new('RGB', (cols * W, rows * (H + 70)), (18, 18, 22))
font = ImageFont.truetype({bold!r}, 16)
small = ImageFont.truetype({regular!r}, 13)
for i, s in enumerate(shots):
    im = Image.open('{stills}/%04d.png' % s['f']).convert('RGB').resize((W, H))
    x, y = (i % cols) * W, (i // cols) * (H + 70)
    sheet.paste(im, (x, y))
    d = ImageDraw.Draw(sheet)
    d.text((x + 6, y + H + 4), f"{{s['id']}}  {{s['size']}}  [{{s['beat']}}]", fill=(255, 214, 110), font=font)
    d.text((x + 6, y + H + 26), s['text'][:42], fill=(230, 230, 230), font=small)
    d.text((x + 6, y + H + 44), s['text'][42:84], fill=(230, 230, 230), font=small)
sheet.save('{out}/storyboard.jpg', quality=88)
"""
    sheet = out / "storyboard.jpg"
    sheet.unlink(missing_ok=True)
    r = run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", script], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-1500:], r.stderr[-800:])
    if a.animatic:
        mix = scratch_mix(a.slug, out)
        encode(out / "frames", mix, out / "animatic.mp4", plan.fps, None, crf=26)
    ok = sheet.exists()
    lay_ok, lay_why = layout_current(a.slug)
    stamp(a.slug, "board", ok, dict(sheet=str(sheet), layout_current=lay_ok))
    gate(ok, "board", f"storyboard: {sheet}")
    if not lay_ok:
        print(f"WARNING [board] {lay_why}: this sheet shows staging that has not passed QC. "
              f"Run `studio.py layout {a.slug}` before judging it; a board review cannot approve until layout passes.")
    rv_ok, why = review_status(a.slug, "board")
    if not rv_ok:
        shots = reports(a.slug) / "shots.txt"
        print(f"CHECKPOINT [review.board] {why}. Look at {sheet}"
              + (f" (and read {shots}; if you cannot view images, review from it and say so)" if shots.exists() else "")
              + f', then record it:\n  python3 scripts/studio.py review {a.slug} board --verdict approve|revise --saw "what you saw"')


def cmd_repair(a):
    """Propose, apply or undo a deterministic staging repair (kit/repair.py)."""
    begin()
    from kit import repair
    sp_path = ep_dir(a.slug) / "screenplay.json"
    prop_path = reports(a.slug) / "repair.json"
    log_path = ep_dir(a.slug) / "repairs.jsonl"
    sp, timing = load(a.slug)
    if a.undo:
        recs = [json.loads(x) for x in log_path.read_text().splitlines()] if log_path.exists() else []
        live = [r for r in recs if r.get("action") == "apply" and not any(u.get("undoes") == r["id"] for u in recs)]
        if not live:
            sys.exit("no applied repair to undo")
        last = live[-1]
        new, kept = repair.undo(sp, last)
        sp_path.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
        with open(log_path, "a") as f:
            f.write(json.dumps(dict(action="undo", undoes=last["id"], at=time.strftime("%Y-%m-%d %H:%M:%S"), kept=kept)) + "\n")
        history(a.slug, dict(stage="repair", what="undo", id=last["id"], at=time.strftime("%Y-%m-%d %H:%M:%S"), t=round(time.time(), 1)))
        print(f"reverted repair {last['id']} ({len(last['changes']) - len(kept)} fields)"
              + (f"; left {len(kept)} fields edited since: {kept}" if kept else "") + f". Run `studio.py layout {a.slug}`.")
        return
    if a.apply:
        if not prop_path.exists():
            sys.exit(f"no proposal; run `studio.py repair {a.slug}` first")
        prop = json.loads(prop_path.read_text())
        if not prop["changes"]:
            sys.exit("the proposal has no changes")
        try:
            new, rec = repair.apply(sp, prop)
        except ValueError as e:
            sys.exit(str(e))
        rec.update(action="apply", id=f"r{int(time.time())}", errors_before=prop["errors_before"], errors_after=prop["errors_after"])
        sp_path.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
        with open(log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        history(a.slug, dict(stage="repair", what="apply", id=rec["id"], changes=len(rec["changes"]),
                             at=time.strftime("%Y-%m-%d %H:%M:%S"), t=round(time.time(), 1)))
        print(f"applied repair {rec['id']}: {len(rec['changes'])} staging fields changed (undo: `studio.py repair {a.slug} --undo`).\n"
              f"Now run `studio.py layout {a.slug}`: the layout gate is the judge, not the repair.")
        return
    if timing is None:
        sys.exit("record the voice first (`studio.py voice`); repairs are searched against real timing")
    print(f"searching for mechanical staging repairs (at most {a.max_evals} candidate plans or {a.max_seconds} s; "
          f"each candidate is a full compile, so this takes minutes)...", flush=True)
    prop = repair.propose(sp, timing, max_evals=a.max_evals, workers=a.workers, max_seconds=a.max_seconds,
                          log=lambda m: print(m, flush=True))
    prop_path.write_text(json.dumps(prop, indent=1))
    for c in prop.get("changes", []):
        print(f"  {'/'.join(map(str, c['path']))}: {c['old'] if not c.get('absent') else '(default)'} -> {c['new']}   [{c['why']}]")
    for u in prop.get("unresolved", []):
        print(f"  unresolved: {' '.join(u['group'])}: {u['errors']} errors, e.g. {u['examples'][0]}")
    if not prop.get("changes"):
        gate(False, "repair", prop.get("reason") or "no mechanical repair found; restage by hand")
    print(f"collision/foot errors {prop['errors_before']} -> {prop['errors_after']} in {prop['evaluations']} candidate plans, {prop['seconds']} s"
          + (" (stopped at the time limit; the proposal is partial)" if prop.get("stopped") else "") + ".")
    print(f"Proposal written to {prop_path}. Apply with `studio.py repair {a.slug} --apply`, then run the layout gate.")


def cmd_catalog(a):
    """What a screenplay can use, read from the engine (kit/catalog.py)."""
    from kit import catalog
    data = catalog.catalog(a.section)
    if a.json or a.section in (None, "actions", "sets"):
        print(json.dumps(data, indent=1, ensure_ascii=False))
    else:
        print(json.dumps(data, ensure_ascii=False))


def cmd_schema(a):
    """JSON Schema for screenplay.json; with a slug, validate that episode against it."""
    from kit import catalog
    sch = catalog.schema()
    if not a.slug:
        print(json.dumps(sch, indent=1))
        return
    errs = catalog.validate(load(a.slug)[0], sch)
    for e in errs:
        print("ERROR:", e)
    gate(not errs, "schema", f"{len(errs)} problems in episodes/{a.slug}/screenplay.json")


def scratch_mix(slug, out):
    """Dialogue-only scratch track for animatics."""
    r = run([AUDIO_PY, KIT / "sound.py", ep_dir(slug), out])
    return out / "sound/mix.wav" if r.returncode == 0 else None


def cmd_render(a):
    begin()
    out = out_dir(a.slug, a.preset)
    full = not a.frames
    if full:
        ok, reasons = freshness(a.slug, "board")
        rv_ok, why = review_status(a.slug, "board")
        if not (ok and rv_ok):
            problem = "; ".join(([f"board: {', '.join(reasons)}"] if not ok else []) + ([f"board review: {why}"] if not rv_ok else []))
            if not a.unreviewed:
                gate(False, "render", f"the storyboard checkpoint is not current ({problem}). Run `studio.py board {a.slug}`, "
                     f"look at the sheet and record `studio.py review {a.slug} board ...`, or pass --unreviewed \"why\".", code=3)
            manual(a.slug, f"render.{a.preset} without a current board review ({problem})", a.unreviewed)
            print(f"note: rendering without a current storyboard review: {a.unreviewed}")
        kept, removed = prune_frames(out, keep_plan=predicted_plan_hash(a.slug))
        print(f"frames: kept {kept} already rendered from this plan, removed {removed} stale or unfinished")
    else:
        f0, f1 = map(int, a.frames.split("-"))
        kept, removed = prune_frames(out, frame_range=(f0, f1))
        print(f"frames: removed {removed} in {f0}-{f1} so they are rendered again")
    extra = ["--render"] + (["--frames", a.frames] if a.frames else [])
    t0 = time.time()
    blender_build(a.slug, a.preset, extra)
    record_frames(out, since=t0)
    plan = json.loads((out / "plan.json").read_text())
    problems = check_frames(out, plan["frames"])
    n = len(frame_files(out))
    secs = time.time() - t0
    stamp(a.slug, f"render.{a.preset}", not problems, dict(frames=n, expected=plan["frames"], partial=a.frames,
                                                         render_seconds=round(secs, 1), unreviewed=a.unreviewed,
                                                         problems=problems))
    for p in problems:
        print("  frames:", p)
    gate(not problems or bool(a.frames), "render", f"{n}/{plan['frames']} frames in {out / 'frames'}" +
         ("" if not problems else " (partial render: the film is not deliverable until every frame is current)"))


def cmd_sound(a):
    begin()
    out = out_dir(a.slug, a.preset)
    if not (out / "plan.json").exists():
        sys.exit("build or render this preset first")
    r = run([AUDIO_PY, KIT / "sound.py", ep_dir(a.slug), out])
    gate(r.returncode == 0, "sound", "stems and mix written")
    # Intelligibility through the full mix: every line must still be understood.
    chk = run([ASR_PY, KIT / "listen.py", out / "plan.json", out / "sound/mix.wav"], capture_output=True, text=True)
    res = json.loads(chk.stdout) if chk.returncode == 0 else {"worst": 0, "lines": {}}
    (reports(a.slug) / f"listen.{a.preset}.json").write_text(json.dumps(res, indent=1))
    for k, v in res["lines"].items():
        print(f"  {k}: {v['match']:.2f}  heard: {v['heard']}")
    stamp(a.slug, f"sound.{a.preset}", res["worst"] >= 0.6, dict(worst_line_match=res["worst"]))
    gate(res["worst"] >= 0.6, "sound", f"worst line intelligibility through the mix: {res['worst']:.2f}")


def encode(frames_dir, audio, dest, fps, ass, crf=18):
    vf = []
    if ass:
        bold, _ = fonts.require("bold")
        vf.append(f"subtitles={ass}:fontsdir={Path(bold).parent}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", fps, "-start_number", 1, "-i", frames_dir / "f_%04d.png"]
    if audio:
        cmd += ["-i", audio]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", crf, "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", 48000, "-shortest"]
    cmd += [dest]
    r = run(cmd)
    if r.returncode:
        sys.exit("encode failed")


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def mix_problems(slug, preset):
    """Is the mix on disk the one the sound gate passed, made for the current plan and takes?"""
    out = out_dir(slug, preset)
    if not (out / "sound/mix.wav").exists():
        return ["no mix; run `studio.py sound`"]
    rec = load_status(slug).get(f"sound.{preset}")
    if not rec:
        return ["the sound gate has not run for this preset"]
    if not rec["ok"]:
        return ["the sound gate failed for this mix"]
    ch = changed_inputs(slug, f"sound.{preset}", rec)
    if ch is None:
        return ["the sound stamp predates input tracking; re-run `studio.py sound`"]
    if ch:
        return [f"the mix was made from different inputs ({', '.join(ch)}); re-run `studio.py sound`"]
    return []


def pacing_verdict(report, plan, sp, bar=None):
    """Judge a pacing audit with the screenplay's declared holds. Returns (hard, artistic, excused)."""
    import video_audit
    return video_audit.judge(report, bar or QUALITY, video_audit.holds_from(plan, sp))


def cmd_deliver(a):
    begin()
    from kit import captions
    import video_audit
    out = out_dir(a.slug, a.preset)
    plan = json.loads((out / "plan.json").read_text())
    sp, _ = load(a.slug)
    problems = check_frames(out, plan["frames"])
    for p in problems:
        print("  frames:", p)
    gate(not problems, "deliver", f"{plan['frames']} rendered frames, all current" if not problems else "rendered frames are not all current")
    mp = mix_problems(a.slug, a.preset)
    gate(not mp, "deliver", "; ".join(mp) or "mix present and current")
    ass = out / "captions.ass"
    ass.write_text(captions.build(plan, sp.get("narrator"), sp.get("cast"), font=fonts.require("bold")[1]))
    movie = out / f"{a.slug}-{a.preset}.mp4"
    mix = out / "sound/mix.wav"
    encode(out / "frames", mix, movie, plan["fps"], ass, crf=17 if a.preset == "final" else 20)
    clean = out / f"{a.slug}-{a.preset}-clean.mp4"
    encode(out / "frames", mix, clean, plan["fps"], None, crf=17 if a.preset == "final" else 20)
    # Technical QC on the actual encode
    info = probe(movie)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(movie), "-f", "null", "-"], capture_output=True, text=True)
    lufs = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(movie), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    import re
    I = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", lufs)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", lufs)[-1])
    exp_w, exp_h = {"final": (1080, 1920), "final_hq": (1080, 1920), "preview": (540, 960), "board": (360, 640)}[a.preset]
    tech = dict(codec=v["codec_name"], pix_fmt=v["pix_fmt"], size=[v["width"], v["height"]], fps=v["r_frame_rate"],
                duration=float(info["format"]["duration"]), audio=bool(au), decode_errors=dec.stderr.strip()[:500],
                lufs=I, true_peak=tp)
    # Pacing audit on the clean picture: hard failures are defects, artistic notes go to the review.
    pace = video_audit.audit(clean)
    hard, artistic, excused = pacing_verdict(pace, plan, sp)
    pace.update(hard=hard, artistic=artistic, excused=excused)
    (reports(a.slug) / f"pacing.{a.preset}.json").write_text(json.dumps(pace, indent=1))
    checks = {
        "h264 yuv420p": v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p",
        "dimensions": (v["width"], v["height"]) == (exp_w, exp_h),
        "24 fps": v["r_frame_rate"] in ("24/1",),
        "audio stream": bool(au),
        "decodes to the end": not dec.stderr.strip(),
        "loudness -14 +-1.5 LUFS": abs(I + 14) <= 1.5,
        "true peak <= -1 dBTP": tp <= -1.0,
        "duration matches plan": abs(tech["duration"] - plan["frames"] / plan["fps"]) < 0.25,
        "no hard pacing failures": not hard,
    }
    # Contact sheet of the captioned film (one frame per shot mid-point)
    sheet = out / "frames.jpg"
    mids = [round((s["t0"] + s["t1"]) / 2, 2) for s in plan["shots"]]
    sel = "+".join(f"between(t\\,{m - 0.02}\\,{m + 0.02})" for m in mids)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(movie), "-vf", f"select='{sel}',scale=180:320,tile=8x3", "-frames:v", "1", "-fps_mode", "vfr", str(sheet)])
    rep = dict(movie=str(movie), clean=str(clean), tech=tech, checks=checks,
               pacing={k: v for k, v in pace.items() if k != "shot_list"}, sheet=str(sheet))
    (reports(a.slug) / f"deliver.{a.preset}.json").write_text(json.dumps(rep, indent=1))
    for k, ok in checks.items():
        print(("  ok   " if ok else "  FAIL ") + k)
    for f in hard:
        print("  FAIL pacing", f["code"], "-", f["msg"])
    for f in excused:
        print("  held", f["msg"])
    for f in artistic:
        print("  note", f["code"], "-", f["msg"])
    allok = all(checks.values())
    stamp(a.slug, f"deliver.{a.preset}", allok, dict(movie=str(movie), failed=[k for k, ok in checks.items() if not ok],
                                                    hard=[f["code"] for f in hard], artistic=[f["code"] for f in artistic]))
    gate(allok, "deliver", f"{movie}")
    print(f"CHECKPOINT [review.picture.{a.preset}] watch {movie} at phone size and look at {sheet}, then record it:\n"
          f'  python3 scripts/studio.py review {a.slug} picture --preset {a.preset} --verdict approve|revise --saw "..."'
          + "".join(f' --note {f["code"]}="..."' for f in artistic))


def cmd_make(a):
    for stage in ("story", "voice", "layout"):
        globals()["cmd_" + stage](argparse.Namespace(slug=a.slug, force=None, quick=False))
    # The board only re-renders when its inputs changed, so an approved review stays valid.
    if not freshness(a.slug, "board")[0] or not (out_dir(a.slug, "board") / "storyboard.jpg").exists():
        cmd_board(argparse.Namespace(slug=a.slug, animatic=False))
    rv_ok, why = review_status(a.slug, "board")
    gate(rv_ok, "review.board", f"storyboard checkpoint: {why}" + ("" if rv_ok else
         f". Look at {out_dir(a.slug, 'board') / 'storyboard.jpg'} and run "
         f"`studio.py review {a.slug} board --verdict approve --saw \"...\"`, then `make` again."), code=3)
    cmd_render(argparse.Namespace(slug=a.slug, preset=a.preset, frames=None, unreviewed=None))
    cmd_sound(argparse.Namespace(slug=a.slug, preset=a.preset))
    cmd_deliver(argparse.Namespace(slug=a.slug, preset=a.preset))


# ---------------------------------------------------------------- status and retrospective
def stage_order(st):
    presets = sorted({split_stage(k)[1] for k in st if split_stage(k)[1]} | {"preview"}, key=lambda p: ("preview", "final", "final_hq").index(p) if p in ("preview", "final", "final_hq") else 9)
    order = ["story", "voice", "layout", "board", "review.board"]
    for p in presets:
        order += [f"render.{p}", f"sound.{p}", f"deliver.{p}", f"review.picture.{p}"]
    return order


def retro_state(slug):
    p = ep_dir(slug) / "retrospective.md"
    if not p.exists():
        return "missing (copy studio/templates/retrospective.md)"
    import re
    m = re.search(r"Produced without modifying the core engine:\s*\**\s*(yes|no)\b", p.read_text(), re.I)
    return f"answered ({m.group(1).lower()})" if m else "verdict not answered"


def cmd_status(a):
    st = load_status(a.slug)
    memo = {}
    for k in stage_order(st):
        if k not in st:
            print(f"  {k:22} not run")
            continue
        s = st[k]
        ok, reasons = freshness(a.slug, k, st, memo)
        extra = [r for r in reasons if r != "failed"]
        print(f"  {k:22} {'PASS' if s['ok'] else 'FAIL'}  {s['at']}" + (f"  STALE: {'; '.join(extra)}" if extra else ""))
    for kind, preset in [("board", None)] + [("picture", p) for p in ("preview", "final") if f"deliver.{p}" in st]:
        print(f"  review {kind}{'.' + preset if preset else '':10} {review_status(a.slug, kind, preset)[1]}")
    print(f"  retrospective          {retro_state(a.slug)}")
    done = freshness(a.slug, "review.picture.final", st, memo)[0] and review_status(a.slug, "picture", "final")[0]
    print("  DONE (final delivered, reviewed and current)" if done else "  not done")


def _git(*args):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def measured_retro(slug):
    """The measured half of the retrospective, from history.jsonl, reviews.json and git."""
    hp = reports(slug) / "history.jsonl"
    events = [json.loads(l) for l in hp.read_text().splitlines() if l.strip()] if hp.exists() else []
    stages = [e for e in events if e["stage"] != "manual"]
    out = ["_Generated by `studio.py retro`; edits here are overwritten._", ""]
    if not events:
        out.append("No production history recorded (history.jsonl starts with the first stage run after Nov 2026).")
    else:
        first, last = events[0]["at"], events[-1]["at"]
        out += [f"History: {len(stages)} stage runs from {first} to {last}.", "",
                "| Stage | Runs | Passed | Failed | Total time | Last |", "| --- | --- | --- | --- | --- | --- |"]
        for k in sorted({e["stage"] for e in stages}, key=lambda k: (stage_order({k: 1}).index(k) if k in stage_order({k: 1}) else 99, k)):
            ev = [e for e in stages if e["stage"] == k]
            secs = sum(e.get("seconds", 0) for e in ev)
            out.append(f"| {k} | {len(ev)} | {sum(e['ok'] for e in ev)} | {sum(not e['ok'] for e in ev)} | "
                       f"{secs / 60:.1f} min | {'pass' if ev[-1]['ok'] else 'FAIL'} |")
        renders = [e for e in stages if e["stage"].startswith("render.") and e.get("render_seconds") and e.get("frames")]
        if renders:
            out += ["", "Render times: " + "; ".join(f"{e['stage']} {e['frames']} frames in {e['render_seconds'] / 60:.1f} min "
                                                   f"({e['render_seconds'] / max(1, e['frames']):.2f} s/frame){' partial' if e.get('partial') else ''}"
                                                   for e in renders[-6:])]
        fails = [e for e in stages if not e["ok"]]
        out += ["", f"Quality-control failures: {len(fails)}"]
        for e in fails[-20:]:
            detail = {k: e[k] for k in ("n_errors", "errors", "error_rules", "unclear", "worst_line_match", "failed", "hard", "problems", "verdict") if e.get(k)}
            out.append(f"- {e['at']} {e['stage']}: {json.dumps(detail)}")
        sp_versions = len({e["inputs"].get("screenplay") for e in stages if e.get("inputs", {}).get("screenplay")})
        kit_versions = len({e["inputs"].get("engine.kit") for e in stages if e.get("inputs", {}).get("engine.kit")})
        out += ["", f"Screenplay versions seen by the gates: {sp_versions}. Engine (kit) versions seen: {kit_versions}"
                + (" (the engine changed during production)." if kit_versions > 1 else ".")]
        mans = [e for e in events if e["stage"] == "manual"]
        out += ["", f"Recorded manual interventions (gate overrides): {len(mans)}"] + [f"- {e['at']} {e['what']}: {e['why']}" for e in mans]
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(events[0]["t"]))
        log = _git("log", f"--since={since}", "--date=short", "--format=- %h %ad %s", "--", "scripts/kit", "scripts/studio.py", "scripts/video_audit.py")
        out += ["", "Engine commits since production started:", log or "- none committed"]
    dirty = _git("status", "--short", "--", "scripts/kit", "scripts/studio.py", "scripts/video_audit.py")
    if dirty:
        out += ["", "Uncommitted engine changes right now:", *[f"- `{l.strip()}`" for l in dirty.splitlines()]]
    revs = load_reviews(slug)
    out += ["", f"Reviews recorded: {len(revs)}"] + [f"- {r['at']} {r['kind']}{'.' + r['preset'] if r.get('preset') else ''}: "
                                                    f"{r['verdict']} ({r['reviewer']})" for r in revs]
    return "\n".join(out)


def cmd_retro(a):
    p = ep_dir(a.slug) / "retrospective.md"
    if not p.exists():
        sp, _ = load(a.slug)
        p.write_text((ROOT / "studio/templates/retrospective.md").read_text().replace("TITLE", sp["title"]).replace("SLUG", a.slug)
                     .replace("STARTED", "unknown (retrospective added after production)"))
    text = p.read_text()
    a0, a1 = "<!-- measured:start -->", "<!-- measured:end -->"
    if a0 not in text:
        text = text.rstrip() + f"\n\n## Measured\n\n{a0}\n{a1}\n"
    i, j = text.index(a0) + len(a0), text.index(a1)
    p.write_text(text[:i] + "\n" + measured_retro(a.slug) + "\n" + text[j:])
    print(f"updated {p}; retrospective: {retro_state(a.slug)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("slug")
    p.add_argument("--title")
    for name in ("story", "status", "retro"):
        sub.add_parser(name).add_argument("slug")
    p = sub.add_parser("voice")
    p.add_argument("slug")
    p.add_argument("--force", nargs="*")
    p = sub.add_parser("layout")
    p.add_argument("slug")
    p.add_argument("--quick", action="store_true", help="every 2nd frame, no foot-slide check; informational, not recorded as the gate")
    p = sub.add_parser("catalog", help="what a screenplay can use: sets, actions, expressions, camera, sound, looks, voices")
    p.add_argument("section", nargs="?", choices=["sets", "actions", "expressions", "camera", "sound", "looks", "voices"])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("schema", help="print the screenplay JSON Schema, or validate SLUG against it")
    p.add_argument("slug", nargs="?")
    p = sub.add_parser("repair", help="propose / apply / undo mechanical staging fixes for layout errors")
    p.add_argument("slug")
    p.add_argument("--apply", action="store_true", help="apply the last proposal to the screenplay")
    p.add_argument("--undo", action="store_true", help="revert the last applied repair")
    p.add_argument("--max-evals", type=int, default=240, help="bound on candidate plans tried")
    p.add_argument("--workers", type=int, default=2, help="parallel candidate checks (CPU processes)")
    p.add_argument("--max-seconds", type=int, default=240, help="stop searching after this long and propose what was found")
    p = sub.add_parser("board")
    p.add_argument("slug")
    p.add_argument("--animatic", action="store_true")
    p = sub.add_parser("review")
    p.add_argument("slug")
    p.add_argument("kind", choices=["board", "picture"])
    p.add_argument("--preset", default="preview", choices=["preview", "final", "final_hq"])
    p.add_argument("--verdict", required=True, choices=["approve", "revise"])
    p.add_argument("--saw", required=True, help="what you saw: shot ids, seconds, what reads and what does not")
    p.add_argument("--note", action="append", help='CODE="disposition" for each artistic note from deliver')
    p.add_argument("--reviewer", default="director")
    for name in ("render", "sound", "deliver", "make"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        p.add_argument("--preset", default="preview", choices=["preview", "final", "final_hq"])
        if name == "render":
            p.add_argument("--frames")
            p.add_argument("--unreviewed", metavar="WHY", help="render without a current storyboard review (recorded as a manual intervention)")
    a = ap.parse_args()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
