"""Deterministic staging repair: bounded candidate search, validated by the layout QC.

    python3 scripts/studio.py repair SLUG            # propose (writes renders/SLUG/reports/repair.json)
    python3 scripts/studio.py repair SLUG --apply    # apply the proposal, keep an undo record
    python3 scripts/studio.py repair SLUG --undo     # revert the last applied repair

What it may change (mechanical staging only):
  - a coordinate an actor walks, approaches, stands up or starts at (`to`, scene cast `at`)
  - the `gap` of an approach
  - a hug's contact point and height together (`at`: hips / chest / head, `lift`)
What it never changes: lines, timing, expressions, looks, badges, cameras, shot list, which
action happens, who is in a scene, or anything else a director chose. A repair is proposed
first, applied only when asked, recorded with its inverse, and validated by the full
layout gate afterwards like any hand edit.

Method: run collision/foot QC without cameras; group errors by (scene, actor); for the
earliest group, find the screenplay fields that place that actor (the last movement before
the error in that scene, the scene's start mark, an active hug); try candidate values in
order of size (rings of 0.1-0.8 m around a coordinate, a few contact values), each
validated by compiling the plan and re-running QC over that scene; keep the smallest change
that clears the group without adding errors; repeat. Bounded by `max_evals`.
"""

import copy
import hashlib
import json
import math
import time
from multiprocessing import get_context

RULES = ("collision.actor", "collision.set", "feet.sink", "feet.slide")
POSITION_VERBS = ("walk", "approach", "stand", "sit", "run")
RINGS = (0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8)
DIRS = 8
HUG_AT = ("hips", "chest", "head")
HUG_LIFT = (0.0, 0.06, 0.12, 0.18)


def sp_hash(sp):
    return hashlib.sha1(json.dumps(sp, sort_keys=True).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ evaluation

_CTX = {}


def _init(timing):
    _CTX["timing"] = timing


def evaluate(sp, window=None, timing=None):
    """Collision/foot errors for a screenplay (no cameras). Returns a list of compact dicts."""
    from .layout import compile_plan, CompileError
    from . import qc
    timing = _CTX.get("timing") if timing is None else timing
    try:
        plan = compile_plan(sp, timing, solve_cameras=False)
    except CompileError as e:
        return None, str(e)
    except Exception as e:  # an engine crash on this candidate: reject it, keep searching
        return None, f"{type(e).__name__}: {e}"
    rep = qc.run(plan, step=2, window=window, shot_checks=False)
    errs = [dict(rule=i["rule"], shot=i["shot"], t0=i["t0"], t1=i["t1"], msg=i["msg"], actor=_actor(i["msg"]), other=_other(i["msg"]))
            for i in rep.merged() if i["severity"] == "error" and i["rule"] in RULES]
    spans = {}
    for s in plan.shots:
        a, b = spans.get(s["scene"], (s["t0"], s["t1"]))
        spans[s["scene"]] = (min(a, s["t0"]), max(b, s["t1"]))
    return errs, spans


def _eval_job(args):
    sp, window = args
    errs, extra = evaluate(sp, window)
    return errs, extra


def _actor(msg):
    return msg.split(".")[0].split(" ")[0]


def _other(msg):
    if " inside " not in msg:
        return None
    rhs = msg.split(" inside ", 1)[1].strip()
    return None if rhs.startswith("'") else rhs.split(".")[0]


# ------------------------------------------------------------------ knobs

def _shots(sp):
    """[(scene_index, shot_index, scene, shot)] in film order."""
    return [(i, j, sc, s) for i, sc in enumerate(sp["scenes"]) for j, s in enumerate(sc["shots"])]


def knobs(sp, group, at_scene_start=False):
    """Screenplay fields that place `group.actor` (and its partner) in `group.scene`, nearest first."""
    out = []
    if at_scene_start:
        sc0 = next(sc for sc in sp["scenes"] if sc["id"] == group["scene"])
        c = sc0.get("cast", {}).get(group["actor"])
        if isinstance(c, dict) and "at" in c:
            i0 = sp["scenes"].index(sc0)
            out.append(dict(kind="point", path=["scenes", i0, "cast", group["actor"], "at"], who=group["actor"]))
    scene_i = next(i for i, sc in enumerate(sp["scenes"]) if sc["id"] == group["scene"])
    sc = sp["scenes"][scene_i]
    shot_ids = [s["id"] for s in sc["shots"]]
    last = shot_ids.index(group["shot"]) if group["shot"] in shot_ids else len(shot_ids) - 1
    for who in [group["actor"]] + ([group["other"]] if group.get("other") else []):
        # Hugs active in the error's shot (contact point and height).
        for k, a in enumerate(sc["shots"][last].get("act", [])):
            if a.get("do") == "hug" and (a["who"] == who or a.get("with") == who):
                out.append(dict(kind="hug_contact", path=["scenes", scene_i, "shots", last, "act", k], who=a["who"]))
        # The last movement of this actor up to the error's shot.
        for j in range(last, -1, -1):
            acts = sc["shots"][j].get("act", [])
            hit = [(k, a) for k, a in enumerate(acts) if a["who"] == who and a.get("do") in POSITION_VERBS and "to" in a]
            if hit:
                k, a = hit[-1]
                base = ["scenes", scene_i, "shots", j, "act", k]
                if isinstance(a["to"], list) and len(a["to"]) == 2:
                    out.append(dict(kind="point", path=base + ["to"], who=who))
                elif a.get("do") == "approach":
                    out.append(dict(kind="gap", path=base + ["gap"], who=who))
                break
        # The actor's start mark in this scene.
        c = sc.get("cast", {}).get(who)
        if isinstance(c, dict) and "at" in c:
            out.append(dict(kind="point", path=["scenes", scene_i, "cast", who, "at"], who=who))
    seen, uniq = set(), []
    for k in out:
        key = tuple(map(str, k["path"]))
        if key not in seen:
            seen.add(key)
            uniq.append(k)
    return uniq


def get(sp, path, default=None):
    node = sp
    for p in path:
        try:
            node = node[p]
        except (KeyError, IndexError, TypeError):
            return default
    return node


def put(sp, path, value, merge=False):
    node = sp
    for p in path[:-1]:
        node = node[p]
    if merge:  # value is {field: new or _ABSENT}; path names the act
        tgt = node[path[-1]]
        for k, v in value.items():
            if v is _ABSENT:
                tgt.pop(k, None)
            else:
                tgt[k] = v
    elif value is _ABSENT:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value



_ABSENT = object()


def candidates(sp, knob):
    cur = get(sp, knob["path"], _ABSENT)
    if knob["kind"] == "point":
        x, y = cur
        for r in RINGS:
            for d in range(DIRS):
                a = 2 * math.pi * d / DIRS
                yield [round(x + r * math.cos(a), 3), round(y + r * math.sin(a), 3)], r
    elif knob["kind"] == "gap":
        base = 0.0 if cur is _ABSENT else cur
        for dg in (0.05, 0.1, 0.15, 0.25, 0.35):
            yield round(base + dg, 3), dg
    elif knob["kind"] == "hug_contact":
        # Contact point and height together (one without the other often cannot clear a hug).
        at0, lift0 = cur.get("at", "chest"), cur.get("lift", 0.0)
        combos = []
        for at in HUG_AT:
            for lift in HUG_LIFT:
                if (at, lift) != (at0, lift0):
                    size = (0.0 if at == at0 else 0.5) + abs(lift - lift0)
                    combos.append((size, at, lift))
        for size, at, lift in sorted(combos):
            yield {"at": at, "lift": lift}, round(size, 3)


# ------------------------------------------------------------------ search

def _free_test(sp, timing, scene_id, radius=0.3):
    """A cheap pre-filter: is a set-local point clear of furniture for a standing actor?"""
    from . import sets as setlib
    from .geom import Nav, element_obb
    sc = next(sc for sc in sp["scenes"] if sc["id"] == scene_id)
    try:
        st = setlib.load(sc["set"])
    except Exception:
        return None
    nav = Nav(st, [b for b in (element_obb(e, st.offset) for e in st.elements if e.get("nav")) if b is not None], radius=radius)
    return lambda p: nav.free(st.world((p[0], p[1], 0.0))[:2])


def groups_of(errs, spans, sp):
    shot_scene = {s["id"]: sc["id"] for sc in sp["scenes"] for s in sc["shots"]}
    gs = {}
    for e in errs:
        scene = shot_scene.get(e["shot"])
        key = (scene, e["actor"], e["shot"])
        g = gs.setdefault(key, dict(scene=scene, actor=e["actor"], other=e["other"], shot=e["shot"], t0=e["t0"], n=0, msgs=[]))
        g["n"] += 1
        if e["t0"] < g["t0"]:
            g.update(shot=e["shot"], t0=e["t0"], other=e["other"] or g["other"])
        if len(g["msgs"]) < 3:
            g["msgs"].append(f"{e['shot']} {e['msg']}")
    return sorted(gs.values(), key=lambda g: g["t0"])


def propose(sp, timing, max_evals=240, workers=2, log=print, max_seconds=None):
    """Return a proposal dict; `sp` is not modified."""
    t_start = time.time()
    work = copy.deepcopy(sp)
    errs, spans = evaluate(work, timing=timing)
    if errs is None:
        return dict(ok=False, reason=f"screenplay does not compile: {spans}")
    before = len(errs)
    changes, evals, skipped = [], 0, []
    ctx = get_context("fork")
    with ctx.Pool(workers, initializer=_init, initargs=(timing,)) as pool:
        out_of_time = lambda: max_seconds is not None and time.time() - t_start > max_seconds
        for _round in range(12):
            if not errs or evals >= max_evals or out_of_time():
                break
            gs = [g for g in groups_of(errs, spans, work) if (g["scene"], g["actor"], g["shot"]) not in {tuple(s["group"]) for s in skipped}]
            if not gs:
                break
            g = gs[0]
            window = spans[g["scene"]]
            in_scene = [e for e in errs if e["t0"] >= window[0] - 1e-6 and e["t0"] <= window[1]]
            log(f"  {g['scene']} {g['actor']}: {g['n']} errors from {g['shot']} ({g['msgs'][0]})")
            best = None
            free = _free_test(work, timing, g["scene"])
            before_keys = {(e["rule"], e["actor"], e["other"]) for e in in_scene}
            for knob in knobs(work, g, at_scene_start=g["t0"] - window[0] < 0.3):
                if evals >= max_evals:
                    break
                cands = list(candidates(work, knob))
                if knob["kind"] == "point" and free:
                    cands = [(v, r) for v, r in cands if free(v)]
                # Candidates of one size are tried together (a "tier": one ring of 8 points, or
                # one contact change). If any clears the group, the whole tier is compared and
                # the one leaving the fewest errors in the scene wins: a fix that strands the
                # actor where the next beat cannot work is worse than an equally small one
                # that does not.
                tiers = {}
                for value, size in cands:
                    tiers.setdefault(size, []).append(value)
                for size in sorted(tiers):
                    tier = tiers[size]
                    passing = []
                    for i in range(0, len(tier), workers):
                        batch = tier[i:i + workers]
                        jobs = []
                        for value in batch:
                            trial = copy.deepcopy(work)
                            put(trial, knob["path"], value, merge=knob["kind"] == "hug_contact")
                            jobs.append((trial, window))
                        results = pool.map(_eval_job, jobs)
                        evals += len(jobs)
                        for value, (e2, _) in zip(batch, results):
                            if e2 is None:
                                continue
                            w2 = [e for e in e2 if window[0] - 1e-6 <= e["t0"] <= window[1]]
                            same_shot = [e for e in w2 if e["shot"] == g["shot"] and g["actor"] in (e["actor"], e["other"])]
                            new_kinds = {(e["rule"], e["actor"], e["other"]) for e in w2} - before_keys
                            if not same_shot and not new_kinds and len(w2) < len(in_scene):
                                passing.append(dict(knob=knob, value=value, size=size, after=len(e2), left=len(w2)))
                        if evals >= max_evals or out_of_time():
                            break
                    if passing:
                        best = min(passing, key=lambda c: c["left"])
                        break
                    if evals >= max_evals or out_of_time():
                        break
                if best or out_of_time():
                    break
            if not best:
                log(f"    no mechanical repair found within the search bounds")
                skipped.append(dict(group=[g["scene"], g["actor"], g["shot"]], errors=g["n"], examples=g["msgs"]))
                continue
            merge = best["knob"]["kind"] == "hug_contact"
            if merge:  # record one field-level change per field so apply/undo stay exact
                act = get(work, best["knob"]["path"])
                why = f"{g['n']} errors: " + "; ".join(g["msgs"][:2])
                for k, v in best["value"].items():
                    if act.get(k, _ABSENT) != v:
                        old = act.get(k, _ABSENT)
                        changes.append(dict(path=best["knob"]["path"] + [k], kind="hug_contact", who=best["knob"]["who"],
                                            old=None if old is _ABSENT else old, absent=old is _ABSENT, new=v,
                                            size=best["size"], why=why))
                old = {k: act.get(k) for k in best["value"]}
            else:
                old = get(work, best["knob"]["path"], _ABSENT)
                changes.append(dict(path=best["knob"]["path"], kind=best["knob"]["kind"], who=best["knob"]["who"],
                                    old=None if old is _ABSENT else old, absent=old is _ABSENT, new=best["value"],
                                    size=best["size"], why=f"{g['n']} errors: " + "; ".join(g["msgs"][:2])))
            put(work, best["knob"]["path"], best["value"], merge=merge)
            log(f"    {'/'.join(map(str, best['knob']['path']))}: {old if old is not _ABSENT else '(default)'} -> {best['value']}")
            errs, spans = evaluate(work, timing=timing)
    return dict(ok=not errs, base=sp_hash(sp), stopped="time limit" if out_of_time() and errs else None, result=sp_hash(work), errors_before=before, errors_after=len(errs),
                remaining=[f"{e['shot']} {e['rule']} {e['msg']}" for e in errs[:20]], unresolved=skipped,
                changes=changes, evaluations=evals, seconds=round(time.time() - t_start, 1),
                note="collision/foot errors only, cameras not solved; run the layout gate to validate")


def apply(sp, proposal):
    """Apply a proposal to the screenplay it was made from. Returns (new_sp, undo_record)."""
    if sp_hash(sp) != proposal["base"]:
        raise ValueError("the screenplay changed since this repair was proposed; propose again")
    new = copy.deepcopy(sp)
    for c in proposal["changes"]:
        put(new, c["path"], c["new"])
    return new, dict(base=proposal["base"], result=sp_hash(new), changes=proposal["changes"], applied=time.strftime("%Y-%m-%d %H:%M:%S"))


def undo(sp, record):
    """Revert an applied repair. Fields edited by hand since are left alone and reported."""
    new = copy.deepcopy(sp)
    kept = []
    for c in reversed(record["changes"]):
        if get(new, c["path"], _ABSENT) == c["new"]:
            put(new, c["path"], _ABSENT if c.get("absent") else c["old"])
        else:
            kept.append(c["path"])
    return new, kept
