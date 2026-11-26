"""Story editor's gate: structural checks on a screenplay before production spends time on it.

This does not judge whether the story is moving; the Director and a critic do that with
studio/departments/01-story.md. It blocks the failure modes that made earlier films flat:
no hook, one location, no turn, no payoff, talking heads, unreadable captions.
"""

import json
import re
from pathlib import Path

from .acting import EXPRESSIONS, GESTURES
from .sets import LIBRARY

QUALITY_BAR = Path(__file__).resolve().parents[2] / "studio/quality-bar.json"
MOVES = {"walk", "run", "approach", "turn", "sit", "stand", "crouch", "uncrouch"}


def words(text):
    return len(re.findall(r"[\w']+", text))


def load_bar(path=QUALITY_BAR):
    return json.loads(Path(path).read_text())


def lint(sp, timing=None, plan=None, bar=None):
    bar = bar or load_bar()
    errors, warns, facts = [], [], {}
    for k in ("title", "logline", "theme", "cast", "voices", "lines", "beats", "setups", "scenes"):
        if not sp.get(k):
            errors.append(f"missing '{k}'")
    if errors:
        return dict(errors=errors, warns=warns, facts=facts)

    shots = [sh for sc in sp["scenes"] for sh in sc["shots"]]
    ids = [sh["id"] for sh in shots]
    for sh in shots:
        if "hold" in sh and not (isinstance(sh["hold"], str) and len(sh["hold"].split()) >= 3):
            errors.append(f"{sh['id']}: 'hold' must say why the picture is held still (a sentence, not a flag)")
    if len(set(ids)) != len(ids):
        errors.append("shot ids must be unique")
    lines = {l["id"]: l for l in sp["lines"]}
    used = [e if isinstance(e, str) else e["id"] for sh in shots for e in sh.get("lines", [])]
    for lid in lines:
        if lid not in used:
            warns.append(f"line {lid} is never placed in a shot")
    for l in sp["lines"]:
        n = words(l["text"])
        if n > 14:
            errors.append(f"line {l['id']} has {n} words; split it (captions read in 1-3 word chunks, keep lines <= 14 words)")
        voice = l.get("voice", l["who"])
        if voice not in sp["voices"]:
            errors.append(f"line {l['id']}: no voice '{voice}'")

    # Locations and scenes
    sets = [sc["set"] for sc in sp["scenes"]]
    for s in sets:
        if s not in LIBRARY:
            errors.append(f"unknown set '{s}' (library: {sorted(LIBRARY)})")
    facts["locations"] = len(set(sets))
    if len(set(sets)) < 3:
        errors.append(f"only {len(set(sets))} location(s); a one-minute story needs at least 3 places to move through")
    facts["scenes"] = len(sp["scenes"])
    facts["shots"] = len(shots)

    # Beats and arc
    beats = sp["beats"]
    emo = [b.get("emotion", 0) for b in beats]
    facts["beats"] = len(beats)
    if len(beats) < 5:
        errors.append("fewer than 5 beats: a 45-75 s short needs hook, setup, turn, climax and button")
    if max(emo) - min(emo) < 4:
        errors.append("emotional range across beats is under 4 points: nothing changes for the audience")
    signs = [1 if e > 0 else -1 if e < 0 else 0 for e in emo]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a and b and a != b)
    if flips < 2:
        warns.append("the emotional line flips fewer than twice; consider a reversal before the climax")
    if beats[0]["id"] != "hook" and shots[0].get("beat") != "hook":
        errors.append("the first beat/shot must be the hook")
    beat_ids = {b["id"] for b in beats}
    for sh in shots:
        if sh.get("beat") and sh["beat"] not in beat_ids:
            errors.append(f"{sh['id']}: beat '{sh['beat']}' is not declared")

    # Setups and payoffs
    order = {sid: i for i, sid in enumerate(ids)}
    if len(sp["setups"]) < 2:
        errors.append("declare at least two setups with payoffs (plant and pay off)")
    for s in sp["setups"]:
        a, b = s.get("setup"), s.get("payoff")
        if a not in order or b not in order:
            errors.append(f"setup '{s.get('id')}' points at unknown shots")
        elif order[a] >= order[b]:
            errors.append(f"setup '{s['id']}' pays off before it is planted")

    # Shots: something happens, variety of sizes, cast acting
    sizes = set()
    lead = sp.get("narrator")
    lead_exprs = set()
    static = 0
    for sc in sp["scenes"]:
        for k, v in sc.get("start_expr", {}).items():
            if k == lead:
                lead_exprs.add(v)
        for sh in sc["shots"]:
            cam = sh.get("cam", {})
            if "size" not in cam:
                errors.append(f"{sh['id']}: camera needs a size")
            sizes.add(cam.get("size"))
            acts = sh.get("act", [])
            for a in acts:
                do = a.get("do")
                if do and do not in MOVES | set(GESTURES) | {"look", "face", "expr", "badge", "fx", "sfx", "release"}:
                    errors.append(f"{sh['id']}: unknown action '{do}'")
                if "expr" in a and a["expr"] not in EXPRESSIONS:
                    errors.append(f"{sh['id']}: unknown expression '{a['expr']}'")
                if a.get("who") == lead and "expr" in a:
                    lead_exprs.add(a["expr"])
            moving = any(a.get("do") in MOVES | set(GESTURES) for a in acts)
            if not moving and cam.get("move", "drift") in ("static",):
                static += 1
            if not acts and not sh.get("lines"):
                errors.append(f"{sh['id']}: nothing happens (no action, no line)")
    facts["shot_sizes"] = sorted(x for x in sizes if x)
    facts["lead_expressions"] = sorted(lead_exprs)
    if len(sizes) < 4:
        errors.append(f"only {len(sizes)} shot sizes; use at least 4 (wide, medium, close, insert/over-shoulder)")
    if lead and len(lead_exprs) < 6:
        errors.append(f"the lead shows {len(lead_exprs)} expressions; give them at least 6 across the film")

    # Timing-dependent checks
    if plan is not None:
        dur = plan.duration
        facts["duration"] = round(dur, 2)
        per_min = len(shots) / dur * 60
        facts["shots_per_minute"] = round(per_min, 1)
        longest = max(s["t1"] - s["t0"] for s in plan.shots)
        facts["longest_shot"] = round(longest, 2)
        spoken = sum(words(l["text"]) for l in sp["lines"])
        facts["words_per_minute"] = round(spoken / dur * 60, 1)
        lo, hi = bar["duration_s"]["limit"]
        t_lo, t_hi = bar["duration_s"]["target"]
        if not lo <= dur <= hi:
            errors.append(f"duration {dur:.1f}s is outside {lo}-{hi} s")
        elif not t_lo <= dur <= t_hi:
            warns.append(f"duration {dur:.1f}s is outside the {t_lo}-{t_hi} s target; the picture review must justify it")
        if per_min < 18:
            errors.append(f"{per_min:.1f} shots/min; reference reels cut 18-40 times a minute")
        if longest > 6.0:
            warns.append(f"longest shot {longest:.1f}s; hold only if the moment earns it")
        if facts["words_per_minute"] > 165:
            errors.append(f"{facts['words_per_minute']} words/min is too dense; leave room for the picture")
        first = plan.shots[0]
        first_line = min((l["t0"] for l in plan.lines), default=99)
        if first_line > 0.6:
            errors.append("no line starts in the first 0.6 s: the hook must speak or show the rule immediately")
        if first["t1"] - first["t0"] > 4.5:
            warns.append("hook shot is longer than 4.5 s")
        if facts["locations"] * 60 / dur < 2.5 and dur > 40:
            warns.append("fewer than 2.5 locations per minute; consider another place to move the story")
    if timing:
        for lid, info in timing.get("lines", {}).items():
            if info.get("match", 1) < 0.8:
                warns.append(f"take {lid} may be mispronounced (ASR heard '{info.get('heard')}')")
    return dict(errors=errors, warns=warns, facts=facts)
