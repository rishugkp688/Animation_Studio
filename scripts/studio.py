#!/usr/bin/env python3
"""Blocky Studio: one command per department, each with a gate.

    python3 scripts/studio.py new     SLUG [--title "..."]   scaffold an episode from the template
    python3 scripts/studio.py story   SLUG                   story editor: structure lint
    python3 scripts/studio.py voice   SLUG                   casting/recording: offline TTS + word alignment
    python3 scripts/studio.py layout  SLUG                   layout + animation QC (collisions, feet, cameras)
    python3 scripts/studio.py board   SLUG                   storyboard sheet + fast animatic with scratch sound
    python3 scripts/studio.py render  SLUG --preset preview|final [--frames A-B]
    python3 scripts/studio.py sound   SLUG --preset ...      ambience, foley, score, mix (needs a build)
    python3 scripts/studio.py deliver SLUG --preset ...      captions, encode, technical + pacing QC, sheets
    python3 scripts/studio.py make    SLUG --preset preview  every stage in order; stops at the first failed gate
    python3 scripts/studio.py status  SLUG                   what has been done and what is stale

Generated files go to renders/SLUG/<preset>/. Reports go to renders/SLUG/reports/.
No stage calls a language model; re-rendering costs no credits.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
AUDIO_PY = ROOT / ".audio-tools/venv/bin/python"
ASR_PY = ROOT / ".audio-tools/asr/bin/python"
BLENDER = shutil.which("blender") or "blender"
QUALITY = json.loads((ROOT / "studio/quality-bar.json").read_text())


def ep_dir(slug):
    return ROOT / "episodes" / slug


def out_dir(slug, preset):
    return ROOT / "renders" / slug / preset


def reports(slug):
    d = ROOT / "renders" / slug / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(slug):
    from kit.layout import load_screenplay, load_timing
    e = ep_dir(slug)
    return load_screenplay(e / "screenplay.json"), load_timing(e)


def clean_env():
    """Environment for Blender: drop pyenv/venv overrides so Blender's embedded Python uses the
    system interpreter it was built against (with its own site-packages, e.g. Pillow)."""
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYENV", "_PYENV", "PYTHON", "VIRTUAL_ENV"))}
    env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if ".pyenv" not in p and "/pyenv/" not in p)
    return env


def run(cmd, **kw):
    print("$", " ".join(map(str, cmd)), flush=True)
    if str(cmd[0]) == BLENDER:
        kw.setdefault("env", clean_env())
    return subprocess.run(list(map(str, cmd)), **kw)


def fingerprint(*paths):
    h = hashlib.sha1()
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    h.update(f.read_bytes())
        elif p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def stamp(slug, stage, ok, data):
    p = reports(slug) / "status.json"
    st = json.loads(p.read_text()) if p.exists() else {}
    st[stage] = dict(ok=ok, at=time.strftime("%Y-%m-%d %H:%M:%S"), screenplay=fingerprint(ep_dir(slug) / "screenplay.json"),
                     kit=fingerprint(ROOT / "scripts/kit"), **data)
    p.write_text(json.dumps(st, indent=1))


def gate(ok, stage, msg):
    print(("PASS " if ok else "FAIL ") + f"[{stage}] {msg}", flush=True)
    if not ok:
        sys.exit(2)


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
    print(f"Created {d}/screenplay.json. Next: write it with studio/departments/01-story.md, then `studio.py story {a.slug}`.")


def cmd_story(a):
    from kit.story import lint
    from kit.layout import compile_plan
    sp, timing = load(a.slug)
    plan = None
    try:
        plan = compile_plan(sp, timing, solve_cameras=False)
    except Exception as e:  # compile problems are story problems at this stage
        r = dict(errors=[f"compile: {e}"], warns=[], facts={})
    else:
        r = lint(sp, timing, plan)
    (reports(a.slug) / "story.json").write_text(json.dumps(r, indent=1))
    for w in r["warns"]:
        print("warn:", w)
    for e in r["errors"]:
        print("ERROR:", e)
    print(json.dumps(r["facts"]))
    stamp(a.slug, "story", not r["errors"], dict(facts=r["facts"]))
    gate(not r["errors"], "story", f"{len(r['errors'])} errors, {len(r['warns'])} warnings{' (scratch timing)' if plan and plan.scratch_timing else ''}")


def cmd_voice(a):
    r = run([AUDIO_PY, ROOT / "scripts/kit/voice.py", ep_dir(a.slug)] + (["--force", *a.force] if a.force else []))
    timing = json.loads((ep_dir(a.slug) / "voice/timing.json").read_text())
    bad = [k for k, v in timing["lines"].items() if v.get("match", 1) < 0.8]
    stamp(a.slug, "voice", r.returncode == 0 and not bad, dict(lines=len(timing["lines"]), unclear=bad))
    gate(r.returncode == 0 and not bad, "voice", f"{len(timing['lines'])} takes; unclear: {bad or 'none'}")


def cmd_layout(a):
    from kit.layout import compile_plan
    from kit import qc
    sp, timing = load(a.slug)
    if timing is None:
        print("note: no voice timing yet; using scratch estimates")
    t0 = time.time()
    plan = compile_plan(sp, timing)
    rep = qc.run(plan, step=1 if not a.quick else 2)
    merged = rep.merged()
    errors = [i for i in merged if i["severity"] == "error"]
    out = dict(duration=plan.duration, shots=len(plan.shots), errors=errors, warns=[i for i in merged if i["severity"] != "error"],
               cameras={s["id"]: dict(size=s["camera"]["size"], side=s["camera"]["side"], report=s["camera"]["report"], hide=s["camera"]["hide"]) for s in plan.shots})
    (reports(a.slug) / "layout.json").write_text(json.dumps(out, indent=1))
    for i in merged:
        print(f"{i['severity'][:4]:4} {i['rule']:16} {i['shot'] or '-':5} {i['t0']:6.2f}-{i['t1']:6.2f}s  {i['msg']}" + (f"  (depth {i['depth']} m)" if i.get("depth") else ""))
    stamp(a.slug, "layout", not errors, dict(errors=len(errors), warns=len(out["warns"]), seconds=round(time.time() - t0, 1)))
    gate(not errors, "layout", f"{len(errors)} errors, {len(out['warns'])} warnings over {int(plan.duration * plan.fps)} frames")


def blender_build(slug, preset, extra):
    out = out_dir(slug, preset)
    cmd = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "-P", ROOT / "scripts/kit/blender_build.py", "--", ep_dir(slug), "--preset", preset, "--out", out] + extra
    r = run(cmd)
    if r.returncode:
        sys.exit("blender build failed")
    return out


def cmd_board(a):
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
    # Labelled contact sheet with PIL (via Blender's Python, which ships Pillow).
    script = f"""
import json
from PIL import Image, ImageDraw, ImageFont
shots = {json.dumps([dict(id=s['id'], size=s['cam'].get('size'), beat=s.get('beat'), text=' / '.join(lines.get(e if isinstance(e, str) else e['id'], '') for e in s['spec'].get('lines', [])), f=f) for s, f in zip(plan.shots, frames)])}
W, H = 270, 480
cols = 6
rows = (len(shots) + cols - 1) // cols
sheet = Image.new('RGB', (cols * W, rows * (H + 70)), (18, 18, 22))
font = ImageFont.truetype('/usr/share/fonts/TTF/FiraSans-Bold.ttf', 16)
small = ImageFont.truetype('/usr/share/fonts/TTF/FiraSans-Regular.ttf', 13)
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
    r = run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", script], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-1500:], r.stderr[-800:])
    if a.animatic:
        mix = scratch_mix(a.slug, out)
        encode(out / "frames", mix, out / "animatic.mp4", plan.fps, None, crf=26)
    ok = (out / "storyboard.jpg").exists()
    stamp(a.slug, "board", ok, dict(sheet=str(out / "storyboard.jpg")))
    gate(ok, "board", f"storyboard: {out / 'storyboard.jpg'}")


def scratch_mix(slug, out):
    """Dialogue-only scratch track for animatics."""
    r = run([AUDIO_PY, ROOT / "scripts/kit/sound.py", ep_dir(slug), out])
    return out / "sound/mix.wav" if r.returncode == 0 else None


def cmd_render(a):
    extra = ["--render"] + (["--frames", a.frames] if a.frames else [])
    out = blender_build(a.slug, a.preset, extra)
    n = len(list((out / "frames").glob("f_*.png")))
    plan = json.loads((out / "plan.json").read_text())
    ok = n >= plan["frames"]
    stamp(a.slug, f"render.{a.preset}", ok, dict(frames=n, expected=plan["frames"]))
    gate(ok or bool(a.frames), "render", f"{n}/{plan['frames']} frames in {out / 'frames'}")


def cmd_sound(a):
    out = out_dir(a.slug, a.preset)
    if not (out / "plan.json").exists():
        sys.exit("build or render this preset first")
    r = run([AUDIO_PY, ROOT / "scripts/kit/sound.py", ep_dir(a.slug), out])
    gate(r.returncode == 0, "sound", "stems and mix written")
    # Intelligibility through the full mix: every line must still be understood.
    chk = run([ASR_PY, ROOT / "scripts/kit/listen.py", out / "plan.json", out / "sound/mix.wav"], capture_output=True, text=True)
    res = json.loads(chk.stdout) if chk.returncode == 0 else {"worst": 0, "lines": {}}
    (reports(a.slug) / f"listen.{a.preset}.json").write_text(json.dumps(res, indent=1))
    for k, v in res["lines"].items():
        print(f"  {k}: {v['match']:.2f}  heard: {v['heard']}")
    stamp(a.slug, f"sound.{a.preset}", res["worst"] >= 0.6, dict(worst_line_match=res["worst"]))
    gate(res["worst"] >= 0.6, "sound", f"worst line intelligibility through the mix: {res['worst']:.2f}")


def encode(frames_dir, audio, dest, fps, ass, crf=18):
    vf = []
    if ass:
        vf.append(f"subtitles={ass}:fontsdir=/usr/share/fonts/TTF")
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


def cmd_deliver(a):
    from kit import captions
    out = out_dir(a.slug, a.preset)
    plan = json.loads((out / "plan.json").read_text())
    sp, _ = load(a.slug)
    frames = sorted((out / "frames").glob("f_*.png"))
    gate(len(frames) >= plan["frames"], "deliver", f"{len(frames)}/{plan['frames']} rendered frames present")
    mix = out / "sound/mix.wav"
    gate(mix.exists(), "deliver", "mix present")
    ass = out / "captions.ass"
    ass.write_text(captions.build(plan, sp.get("narrator"), sp.get("cast")))
    title = sp["title"]
    movie = out / f"{a.slug}-{a.preset}.mp4"
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
    checks = {
        "h264 yuv420p": v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p",
        "dimensions": (v["width"], v["height"]) == (exp_w, exp_h),
        "24 fps": v["r_frame_rate"] in ("24/1",),
        "audio stream": bool(au),
        "decodes to the end": not dec.stderr.strip(),
        "loudness -14 +-1.5 LUFS": abs(I + 14) <= 1.5,
        "true peak <= -1 dBTP": tp <= -1.0,
        "duration matches plan": abs(tech["duration"] - plan["frames"] / plan["fps"]) < 0.25,
    }
    # Pacing / life audit against the house quality bar
    pace_json = reports(a.slug) / f"pacing.{a.preset}.json"
    subprocess.run([sys.executable, ROOT / "scripts/video_audit.py", str(clean), "--json", str(pace_json)], capture_output=True, text=True)
    pacing = json.loads(pace_json.read_text()) if pace_json.exists() else {}
    # Contact sheet of the captioned film (one frame per shot mid-point)
    sheet = out / "frames.jpg"
    mids = [round((s["t0"] + s["t1"]) / 2, 2) for s in plan["shots"]]
    sel = "+".join(f"between(t\\,{m - 0.02}\\,{m + 0.02})" for m in mids)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(movie), "-vf", f"select='{sel}',scale=180:320,tile=8x3", "-frames:v", "1", "-fps_mode", "vfr", str(sheet)])
    rep = dict(movie=str(movie), clean=str(clean), tech=tech, checks=checks, pacing=pacing, sheet=str(sheet))
    (reports(a.slug) / f"deliver.{a.preset}.json").write_text(json.dumps(rep, indent=1))
    for k, ok in checks.items():
        print(("  ok   " if ok else "  FAIL ") + k)
    allok = all(checks.values())
    stamp(a.slug, f"deliver.{a.preset}", allok, dict(movie=str(movie)))
    gate(allok, "deliver", f"{movie}")


def cmd_make(a):
    for stage in ("story", "voice", "layout"):
        globals()["cmd_" + stage](argparse.Namespace(slug=a.slug, force=None, quick=False))
    cmd_render(argparse.Namespace(slug=a.slug, preset=a.preset, frames=None))
    cmd_sound(argparse.Namespace(slug=a.slug, preset=a.preset))
    cmd_deliver(argparse.Namespace(slug=a.slug, preset=a.preset))


def cmd_status(a):
    p = reports(a.slug) / "status.json"
    st = json.loads(p.read_text()) if p.exists() else {}
    cur_sp = fingerprint(ep_dir(a.slug) / "screenplay.json")
    cur_kit = fingerprint(ROOT / "scripts/kit")
    order = ["story", "voice", "layout", "board", "render.preview", "sound.preview", "deliver.preview", "render.final", "sound.final", "deliver.final"]
    for k in order:
        if k not in st:
            print(f"  {k:16} not run")
            continue
        s = st[k]
        stale = (s.get("screenplay") != cur_sp) or (s.get("kit") != cur_kit)
        print(f"  {k:16} {'PASS' if s['ok'] else 'FAIL'}{'  (stale: inputs changed)' if stale else ''}  {s['at']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("slug")
    p.add_argument("--title")
    for name in ("story", "status"):
        sub.add_parser(name).add_argument("slug")
    p = sub.add_parser("voice")
    p.add_argument("slug")
    p.add_argument("--force", nargs="*")
    p = sub.add_parser("layout")
    p.add_argument("slug")
    p.add_argument("--quick", action="store_true")
    p = sub.add_parser("board")
    p.add_argument("slug")
    p.add_argument("--animatic", action="store_true")
    for name in ("render", "sound", "deliver", "make"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        p.add_argument("--preset", default="preview", choices=["preview", "final", "final_hq"])
        if name == "render":
            p.add_argument("--frames")
    a = ap.parse_args()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
