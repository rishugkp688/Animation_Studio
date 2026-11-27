"""What a screenplay can use, read from the engine itself (never a second copy).

    python3 scripts/studio.py catalog [sets|actions|expressions|camera|sound|looks|voices] [--json]
    python3 scripts/studio.py schema          # JSON Schema of screenplay.json, enums from this catalog

Sources: the set library (`sets.py`), the motion vocabulary (`acting.py`, `story.py`), the
camera tables (`camera.py`), and, read from source because those modules need Blender or
the audio tools to import, music moods and ambiences (`sound.py`) and hair and outfit
styles (`blender_build.py`). Parameters for each action are listed as they are used in
the finished films, so every example is known to work.
"""

import ast
import json
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parents[1]


def _compares(path, func, var):
    """String constants compared with `var` (== or in) inside `func` of a module, by AST."""
    tree = ast.parse((KIT / path).read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for c in ast.walk(node):
                if isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == var:
                    for comp in c.comparators:
                        for k in ast.walk(comp):
                            if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value not in out:
                                out.append(k.value)
    return out


def _dict_keys(path, name):
    tree = ast.parse((KIT / path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets) and isinstance(node.value, ast.Dict):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    return []


def films():
    out = {}
    for p in sorted((ROOT / "episodes").glob("*/screenplay.json")):
        try:
            out[p.parent.name] = json.loads(p.read_text())
        except ValueError:
            pass
    return out


def sets_catalog():
    from . import sets as setlib
    out = {}
    for sid in setlib.LIBRARY:
        s = setlib.load(sid)
        out[sid] = dict(title=s.title, kind=getattr(s, "kind", None), times=sorted(getattr(s, "times", {}) or {}),
                        default_time=getattr(s, "default_time", None), bounds=[round(v, 2) for v in s.bounds],
                        marks={k: [round(v[0], 2), round(v[1], 2), round(v[2])] for k, v in s.marks.items()},
                        seats={k: [round(v["x"], 2), round(v["y"], 2)] for k, v in s.seats.items()},
                        points=sorted(s.points))
    return out


def actions_catalog():
    from .story import MOVES
    from .acting import GESTURES
    special = {"look": "turn eyes/head to a target", "face": "turn the body to a target", "expr": "change expression",
               "badge": "set the text/number over a head", "fx": "visual effect", "sfx": "sound effect", "release": "end a hug"}
    params = {}
    for slug, sp in films().items():
        for sc in sp.get("scenes", []):
            for sh in sc.get("shots", []):
                for a in sh.get("act", []):
                    d = params.setdefault(a.get("do", "(none: look/expr only)"), {})
                    for k, v in a.items():
                        if k in ("who", "do"):
                            continue
                        d.setdefault(k, v)
    verbs = {}
    for v in sorted(MOVES):
        verbs[v] = dict(kind="move")
    for v in sorted(GESTURES):
        verbs[v] = dict(kind="gesture")
    for v, what in special.items():
        verbs[v] = dict(kind="special", what=what)
    for v in verbs:
        verbs[v]["params_used_in_films"] = params.get(v, {})
    verbs["(none: look/expr only)"] = dict(kind="special", what="an act with no `do` just sets look/expr",
                                           params_used_in_films=params.get("(none: look/expr only)", {}))
    common = dict(who="actor id (or list)", t="seconds into the shot, '+0.3' after this actor's last act, 'line:ID', 'end:ID'",
                  dur="seconds", to="[x, y] in metres in the set, a mark, a seat, a set point or an actor",
                  expr="expression name", look="target: actor, 'actor.part', set point or [x, y, z]")
    return dict(common_params=common, verbs=verbs)


def camera_catalog():
    from . import camera
    moves = ["static", "push", "pull", "push_fast", "drift", "arc", "rise", "track"]
    return dict(sizes=list(camera.SIZE_STUDS) + ["two", "ots", "insert"], heights=list(camera.HEIGHTS), moves=moves,
                relative=["body", "face", "world"],
                side="degrees around the subject from the front of their face, + = toward their left; "
                     "for 'two', 0 = square-on to the pair on the side they face",
                other_params=["key (0-1: storyboard moment)", "lens (mm)", "dist_mul", "world_yaw", "lead", "arc", "fstop"])


def sound_catalog():
    return dict(music_moods=_dict_keys("sound.py", "STYLE"), ambiences=_compares("sound.py", "ambience", "kind"))


def looks_catalog():
    return dict(hair=_compares("blender_build.py", "build_hair", "style"), outfit_top=_compares("blender_build.py", "build_outfit", "style"),
                fields_used_in_films=sorted({k for sp in films().values() for c in sp.get("cast", {}).values() for k in c.get("look", {})}),
                cast_fields_used_in_films=sorted({k for sp in films().values() for c in sp.get("cast", {}).values() for k in c}))


def voices_catalog():
    used = {}
    for sp in films().values():
        for vid, v in sp.get("voices", {}).items():
            used.setdefault(v.get("engine"), set()).add(v.get("speaker"))
    return dict(engines={k: sorted(x for x in v if x) for k, v in used.items()},
                note="speakers listed are the ones used in finished films; other speakers of each engine may work but are unproven",
                params=["engine", "speaker", "seed", "speed", "pitch", "instruct (qwen3)"])


def expressions_catalog():
    from .acting import EXPRESSIONS
    return sorted(EXPRESSIONS)


SECTIONS = dict(sets=sets_catalog, actions=actions_catalog, expressions=expressions_catalog, camera=camera_catalog,
                sound=sound_catalog, looks=looks_catalog, voices=voices_catalog)


def catalog(section=None):
    if section:
        return SECTIONS[section]()
    return {k: f() for k, f in SECTIONS.items()}


def schema():
    """JSON Schema for screenplay.json. Structure plus enums; the story gate checks the rest."""
    c = catalog()
    act = dict(type="object", required=["who", "t"], properties=dict(
        who={"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
        t={"oneOf": [{"type": "number"}, {"type": "string"}]},
        do={"enum": [v for v in c["actions"]["verbs"] if not v.startswith("(")]},
        expr={"enum": c["expressions"]}, dur={"type": "number"}))
    cam = dict(type="object", required=["size", "on"], properties=dict(
        size={"enum": c["camera"]["sizes"]}, height={"enum": c["camera"]["heights"]}, move={"enum": c["camera"]["moves"]},
        relative={"enum": c["camera"]["relative"]}, side={"type": "number"},
        on={"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}))
    shot = dict(type="object", required=["id", "cam"], properties=dict(
        id={"type": "string"}, beat={"type": "string"}, lines={"type": "array"}, cam=cam,
        act={"type": "array", "items": act}, hold={"type": "string", "minLength": 10}, dur={"type": "number"}))
    scene = dict(type="object", required=["id", "set", "shots"], properties=dict(
        id={"type": "string"}, set={"enum": list(c["sets"])}, time={"type": "string"}, cast={"type": "object"},
        shots={"type": "array", "items": shot, "minItems": 1}))
    line = dict(type="object", required=["id", "who", "text"], properties=dict(
        id={"type": "string"}, who={"type": "string"}, text={"type": "string"}, say={"type": "string"},
        kind={"enum": ["vo", "dialogue"]}))
    voice = dict(type="object", required=["engine"], properties=dict(engine={"enum": list(c["voices"]["engines"])}))
    music = dict(type="object", required=["at", "mood"], properties=dict(at={"type": "string"}, mood={"enum": c["sound"]["music_moods"]}))
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Blocky Studio screenplay",
            "description": "Generated by `studio.py schema` from the engine. Structure and vocabulary only; "
                           "the story gate and layout gate check everything else.",
            "type": "object", "required": ["title", "cast", "voices", "lines", "scenes"],
            "properties": dict(title={"type": "string"}, narrator={"type": "string"}, style={"type": "string"},
                               cast={"type": "object"}, voices={"type": "object", "additionalProperties": voice},
                               lines={"type": "array", "items": line}, beats={"type": "array"}, setups={"type": "array"},
                               music={"type": "array", "items": music}, scenes={"type": "array", "items": scene, "minItems": 1})}


_TYPES = dict(object=dict, array=list, string=str, number=(int, float), integer=int, boolean=bool)


def validate(inst, sch, path="$"):
    """Errors for the JSON Schema subset `schema()` uses: type, enum, required, properties,
    additionalProperties, items, minItems, minLength, oneOf. Returns a list of 'path: problem'."""
    errs = []
    if "oneOf" in sch:
        if not any(not validate(inst, s, path) for s in sch["oneOf"]):
            errs.append(f"{path}: {json.dumps(inst)[:60]} matches none of the allowed forms")
        return errs
    t = sch.get("type")
    if t and not (isinstance(inst, _TYPES[t]) and not (t == "number" and isinstance(inst, bool))):
        return [f"{path}: expected {t}, got {type(inst).__name__}"]
    if "enum" in sch and inst not in sch["enum"]:
        errs.append(f"{path}: {json.dumps(inst)} is not one of {', '.join(map(str, sch['enum']))}")
    if isinstance(inst, dict):
        errs += [f"{path}: missing '{k}'" for k in sch.get("required", []) if k not in inst]
        for k, v in inst.items():
            if k in sch.get("properties", {}):
                errs += validate(v, sch["properties"][k], f"{path}.{k}")
            elif isinstance(sch.get("additionalProperties"), dict):
                errs += validate(v, sch["additionalProperties"], f"{path}.{k}")
    if isinstance(inst, list):
        if len(inst) < sch.get("minItems", 0):
            errs.append(f"{path}: needs at least {sch['minItems']} items")
        if "items" in sch:
            for i, v in enumerate(inst):
                errs += validate(v, sch["items"], f"{path}[{i}]")
    if isinstance(inst, str) and len(inst) < sch.get("minLength", 0):
        errs.append(f"{path}: too short (at least {sch['minLength']} characters)")
    return errs
