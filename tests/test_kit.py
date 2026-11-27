"""Unit tests for the pure-Python studio kit (no Blender needed).

    python3 tests/test_kit.py
"""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kit.acting import Performer, Stage, EXPRESSIONS, face_code  # noqa: E402
from kit.rig import part_boxes, Body  # noqa: E402
from kit.geom import overlap_depth, element_obb, segment_hits_obb  # noqa: E402
from kit.layout import compile_plan, load_timing  # noqa: E402
from kit.camera import project  # noqa: E402
from kit import qc, story, sets, fonts, captions  # noqa: E402

EP = ROOT / "episodes/one-hug-left"
FILMS = ["one-hug-left", "three-words"]   # every finished film is a regression fixture


class Locomotion(unittest.TestCase):
    def test_walk_keeps_stance_feet_planted(self):
        st = Stage()
        p = st.add(Performer("a", 0.8))
        p.place(0, (0, 0), 0.0)
        end = p.walk(0.2, [(0, -3), (2, -4)], end_yaw=math.pi / 2)
        prev = None
        worst = 0.0
        for f in range(int(end * 24) + 12):
            q = p.pose(f / 24)
            pts = q["points"]
            if prev:
                for s in "rl":
                    if q["feet_planted"][s] and prev[1][s]:
                        worst = max(worst, math.dist(pts["foot_" + s][:2], prev[0][s][:2]))
            prev = ({s: pts["foot_" + s] for s in "rl"}, q["feet_planted"])
        self.assertLess(worst, 0.022)

    def test_feet_never_sink_while_walking(self):
        st = Stage()
        p = st.add(Performer("a", 1.0))
        p.place(0, (0, 0), 0.0)
        end = p.walk(0.0, [(3, 0)], run=True)
        for f in range(int(end * 24)):
            b = part_boxes(p.body, p.pose(f / 24)["mats"])
            self.assertGreater(min(b["leg_r"].lowest(), b["leg_l"].lowest()), -0.03)

    def test_expressions_resolve(self):
        for name in EXPRESSIONS:
            e, m, x = face_code(name)
            self.assertTrue(0 <= e < 16 and 0 <= m < 16 and 0 <= x < 4)


class Geometry(unittest.TestCase):
    def test_route_to_a_blocked_point_beside_the_actor_is_never_empty(self):
        from kit.geom import Nav
        s = sets.load("grandpa_living")
        nav = Nav(s, [b for b in (element_obb(e, s.offset) for e in s.elements if e.get("nav")) if b is not None])
        a, b = (-2.5, -2.5), (-2.6, -2.5)      # free spot; target 10 cm into the wall's clearance
        self.assertTrue(nav.free(a))
        self.assertFalse(nav.free(b))
        self.assertEqual(nav.route(a, b), [b])  # used to be [], which crashed the walk

    def test_overlap_depth(self):
        from kit.rig import OBB
        ax = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
        a = OBB("a", (0, 0, 0), ax, (0.5, 0.5, 0.5))
        b = OBB("b", (0.8, 0, 0), ax, (0.5, 0.5, 0.5))
        c = OBB("c", (1.2, 0, 0), ax, (0.5, 0.5, 0.5))
        self.assertAlmostEqual(overlap_depth(a, b), 0.2, places=6)
        self.assertEqual(overlap_depth(a, c), 0.0)
        self.assertTrue(segment_hits_obb((-2, 0, 0), (2, 0, 0), a))
        self.assertFalse(segment_hits_obb((-2, 1, 0), (2, 1, 0), a))

    def test_sets_are_r6_scale(self):
        """Doors in the library must let a 1.3 m wide R6 adult through."""
        for sid, fn in sets.LIBRARY.items():
            s = fn()
            for e in s.elements:
                if e["name"].endswith(".leaf") and e["shape"] == "box":
                    w = max(e["size"][0], e["size"][1])
                    self.assertGreaterEqual(w, 1.3, f"{sid}: {e['name']} is {w} m wide")


class EpisodeChecks:
    """Run against every film in FILMS (see the subclasses below)."""
    slug = None

    @classmethod
    def setUpClass(cls):
        e = ROOT / "episodes" / cls.slug
        cls.sp = json.loads((e / "screenplay.json").read_text())
        cls.timing = load_timing(e)
        cls.plan = compile_plan(cls.sp, cls.timing)

    def test_story_gate_passes(self):
        r = story.lint(self.sp, self.timing, self.plan)
        self.assertEqual(r["errors"], [])
        self.assertGreaterEqual(r["facts"]["locations"], 3)

    def test_layout_has_no_errors(self):
        # Every 3rd frame for speed. The foot-slide rule needs consecutive frames, so it is
        # covered by test_walk_keeps_stance_feet_planted and by the layout gate (step 1).
        rep = qc.run(self.plan, step=3)
        errs = [i for i in rep.merged() if i["severity"] == "error"]
        self.assertEqual(errs, [], json.dumps(errs[:5], indent=1))

    def test_close_ups_keep_eyes_in_upper_frame(self):
        for sh in self.plan.shots:
            cam = sh["camera"]
            if cam["size"] not in ("cu", "mcu") or cam["report"]:
                continue
            t = sh["t0"] + (sh["t1"] - sh["t0"]) * sh["cam"].get("key", 0.6)
            fr = min(cam["frames"], key=lambda f: abs(f[0] - t))
            eyes = self.plan.stage.performers[cam["subjects"][0]].pose(t)["points"]["eyes"]
            x, y, _ = project(fr[1], fr[2], fr[3], eyes)
            self.assertTrue(0.15 < x < 0.85 and 0.45 < y < 0.9, f"{sh['id']}: eyes at {x:.2f},{y:.2f}")

    def test_plan_follows_the_screenplay_narrator(self):
        summ = self.plan.summary()
        self.assertEqual(summ["narrator"], self.sp.get("narrator"))
        self.assertTrue(all("reverb" in s for s in summ["shots"]))
        self.assertTrue(captions.build(summ, self.sp.get("narrator"), self.sp.get("cast")).startswith("[Script Info]"))

    def test_duration_is_within_the_hard_limit(self):
        lo, hi = story.load_bar()["duration_s"]["limit"]
        self.assertTrue(lo <= self.plan.duration <= hi, self.plan.duration)


class OneHugLeft(EpisodeChecks, unittest.TestCase):
    slug = "one-hug-left"


class ThreeWords(EpisodeChecks, unittest.TestCase):
    slug = "three-words"


class Generality(unittest.TestCase):
    """Nothing in the engine may depend on one episode's cast or sets."""

    def test_gestures_can_target_set_points(self):
        sp = json.loads((EP / "screenplay.json").read_text())
        sh = sp["scenes"][1]["shots"][0]          # living room: has the 'photo' point
        sh["act"].append({"who": "kai", "t": 0.5, "do": "point", "at": "photo", "dur": 0.8})
        plan = compile_plan(sp, load_timing(EP), solve_cameras=False)
        t = plan.shots[6]["t0"] + 0.9
        self.assertIsNotNone(plan.stage.performers["kai"].pose(t))

    def test_sound_and_captions_follow_the_narrator(self):
        from kit import captions
        sp = json.loads((EP / "screenplay.json").read_text())
        plan = compile_plan(sp, load_timing(EP), solve_cameras=False).summary()
        self.assertEqual(plan["narrator"], "kai")
        self.assertTrue(all("reverb" in s for s in plan["shots"]))
        tints = captions.speaker_tints(plan, sp["cast"])
        self.assertEqual(list(tints)[0], "kai")
        self.assertIn("grandpa", tints)

    def test_every_library_set_declares_sound(self):
        for sid in sets.LIBRARY:
            s = sets.load(sid)
            self.assertIn(s.surface, ("concrete", "wood", "vinyl"))
            self.assertEqual(len(s.reverb), 2)


class StoryGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = json.loads((EP / "screenplay.json").read_text())
        cls.timing = load_timing(EP)
        cls.plan = compile_plan(cls.sp, cls.timing, solve_cameras=False)
        cls.bar = story.load_bar()

    def bar_with(self, target, limit):
        bar = json.loads(json.dumps(self.bar))
        bar["duration_s"].update(target=target, limit=limit)
        return bar

    def test_duration_comes_from_the_quality_bar(self):
        d = self.plan.duration
        inside = story.lint(self.sp, self.timing, self.plan, self.bar_with([d - 5, d + 5], [d - 10, d + 10]))
        self.assertFalse([w for w in inside["warns"] if "duration" in w])
        long_ = story.lint(self.sp, self.timing, self.plan, self.bar_with([d - 20, d - 10], [d - 30, d + 10]))
        self.assertTrue([w for w in long_["warns"] if "target" in w])
        self.assertFalse([e for e in long_["errors"] if "duration" in e])
        too_long = story.lint(self.sp, self.timing, self.plan, self.bar_with([d - 20, d - 10], [d - 30, d - 5]))
        self.assertTrue([e for e in too_long["errors"] if "duration" in e])

    def test_hold_must_say_why(self):
        sp = json.loads(json.dumps(self.sp))
        sh = sp["scenes"][0]["shots"][0]
        sh["hold"] = True
        self.assertTrue([e for e in story.lint(sp, self.timing, self.plan)["errors"] if "hold" in e])
        sh["hold"] = "the silence after the news lands"
        self.assertFalse([e for e in story.lint(sp, self.timing, self.plan)["errors"] if "hold" in e])

    def test_unknown_names_list_the_valid_ones(self):
        sp = json.loads(json.dumps(self.sp))
        sp["scenes"][0]["shots"][0]["act"].append({"who": "kai", "t": 0.0, "do": "cartwheel", "expr": "smug"})
        errs = story.lint(sp, self.timing, self.plan)["errors"]
        act = next(e for e in errs if "cartwheel" in e)
        expr = next(e for e in errs if "smug" in e)
        self.assertIn("walk", act)
        self.assertIn("teary", expr)


class Fonts(unittest.TestCase):
    def test_prefers_fira_then_falls_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fonts.find("bold", dirs=[d], env={}), (None, None))
            (Path(d) / "DejaVuSans-Bold.ttf").write_bytes(b"x")
            self.assertEqual(fonts.find("bold", dirs=[d], env={})[1], "DejaVu Sans")
            (Path(d) / "FiraSans-Bold.otf").write_bytes(b"x")
            self.assertEqual(fonts.find("bold", dirs=[d], env={}), (str(Path(d) / "FiraSans-Bold.otf"), "Fira Sans"))
            own = Path(d) / "Mine-Bold.ttf"
            own.write_bytes(b"x")
            self.assertEqual(fonts.find("bold", dirs=[d], env={"BLOCKY_FONT_BOLD": str(own), "BLOCKY_FONT_FAMILY": "Mine"}),
                             (str(own), "Mine"))

    def test_captions_use_the_font_found(self):
        plan = {"lines": [], "shots": []}
        self.assertIn("Style: VO,Noto Sans,", captions.build(plan, font="Noto Sans"))


# The style seam (Agent_Docs/06-style-seam.md). Modules that are not part of the R6 style
# implementation may reach it only through these names: dimensions, posed geometry, named
# points and the expression/gesture vocabulary. Raw tables, atlas layouts and stud constants
# stay inside the style modules.
STYLE_MODULES = {"rig", "acting", "faces", "blender_build"}
SEAM = {
    "rig": {"Body", "part_boxes", "points", "joint_matrices", "OBB"},
    "acting": {"Performer", "Stage", "EXPRESSIONS", "GESTURES"},
    "faces": set(),
    "blender_build": set(),
}


class StyleSeam(unittest.TestCase):
    def test_body_dimensions_come_only_from_the_rig_tables(self):
        from kit import rig
        old = dict(rig.PARTS), dict(rig.JOINTS)
        try:
            for k in ("leg_r", "leg_l"):
                j, c, sz = rig.PARTS[k]
                rig.PARTS[k] = (j, (0, 0, -1.3), (sz[0], sz[1], 2.6))
            rig.JOINTS["pelvis"] = ("root", (0, 0, 2.6))
            b = Body()
            self.assertAlmostEqual(b.leg / b.s, 2.6)
            self.assertAlmostEqual(b.hip_height / b.s, 2.6)
            self.assertAlmostEqual(b.height / b.s, 2.6 + 2.0 + rig.NECK_GAP + rig.HEAD_HEIGHT)
        finally:
            rig.PARTS.clear(); rig.PARTS.update(old[0])
            rig.JOINTS.clear(); rig.JOINTS.update(old[1])

    def test_r6_dimensions_are_unchanged(self):
        b = Body()
        self.assertEqual([round(v / b.s, 4) for v in (b.leg, b.arm, b.hip_height, b.height, b.eye_height, b.lateral, b.half_depth)],
                         [2.0, 1.55, 2.0, 5.26, 4.66, 0.5, 0.5])

    def test_shared_modules_use_only_the_seam(self):
        import ast
        kit = ROOT / "scripts" / "kit"
        leaks = []
        for f in sorted(kit.glob("*.py")):
            if f.stem in STYLE_MODULES or f.stem == "__init__":
                continue
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module.split(".")[-1]
                    if mod in SEAM:
                        leaks += [f"{f.stem} imports {mod}.{a.name}" for a in node.names if a.name not in SEAM[mod]]
                elif isinstance(node, ast.Import):
                    leaks += [f"{f.stem} imports {a.name}" for a in node.names if a.name.split(".")[-1] in SEAM]
        self.assertEqual(leaks, [], "shared modules reaching past the style seam")


class Catalog(unittest.TestCase):
    """The catalog and schema are read from the engine; both films must validate."""

    def test_both_films_validate_against_the_schema(self):
        from kit import catalog
        sch = catalog.schema()
        for film in FILMS:
            sp = json.loads((ROOT / "episodes" / film / "screenplay.json").read_text())
            self.assertEqual(catalog.validate(sp, sch), [], film)

    def test_schema_errors_name_the_path_and_the_valid_values(self):
        from kit import catalog
        sp = json.loads((EP / "screenplay.json").read_text())
        sp["scenes"][0]["set"] = "moon_base"
        sp["scenes"][0]["shots"][0]["cam"]["size"] = "closeup"
        errs = catalog.validate(sp, catalog.schema())
        self.assertTrue(any(e.startswith("$.scenes[0].set") and "school_gate" in e for e in errs), errs)
        self.assertTrue(any(e.startswith("$.scenes[0].shots[0].cam.size") and "mcu" in e for e in errs), errs)

    def test_catalog_comes_from_the_engine(self):
        from kit import catalog, camera
        c = catalog.catalog()
        self.assertEqual(set(c["sets"]), set(sets.LIBRARY))
        self.assertEqual(set(c["expressions"]), set(EXPRESSIONS))
        self.assertTrue(set(camera.SIZE_STUDS) <= set(c["camera"]["sizes"]))
        used = {cm["look"]["hair"]["style"] for film in FILMS
                for cm in json.loads((ROOT / "episodes" / film / "screenplay.json").read_text())["cast"].values()
                if isinstance(cm.get("look", {}).get("hair"), dict)}
        self.assertTrue(used <= set(c["looks"]["hair"]), used - set(c["looks"]["hair"]))
        self.assertIn("warm", c["sound"]["music_moods"])


class ShotReport(unittest.TestCase):
    """Text review for models without vision (kit/shotreport.py)."""

    @classmethod
    def setUpClass(cls):
        from kit import shotreport
        sp = json.loads((EP / "screenplay.json").read_text())
        # The camera a benchmark agent tried on sh16 (Sonnet, 2026-11-27): Grandpa's back fills the frame.
        for sc in sp["scenes"]:
            for sh in sc["shots"]:
                if sh["id"] == "sh16":
                    sh["cam"].update(side=30, height="eye")
        cls.shots = {s["id"]: s for s in shotreport.describe(compile_plan(sp, load_timing(EP)), sp)}
        cls.text = shotreport.to_text(list(cls.shots.values()))

    def test_a_face_hidden_behind_another_actor_is_flagged(self):
        flags = self.shots["sh16"]["flags"]
        self.assertTrue(any("kai's face is behind grandpa" in f for f in flags), flags)

    def test_a_clean_close_up_reads_front_on_and_unflagged(self):
        sh = self.shots["sh03"]
        kai = next(a for a in sh["actors"] if a["id"] == "kai")
        self.assertEqual(kai["facing"], "front")
        self.assertTrue(kai["face_in_frame"])
        self.assertEqual(sh["flags"], [])

    def test_text_lists_lines_and_flags(self):
        self.assertIn('says  kai: "I got zero."', self.text)
        self.assertIn("!  subject kai's face is behind grandpa", self.text)


class Repair(unittest.TestCase):
    """Deterministic staging repair (kit/repair.py): bounded, minimal, reversible."""

    def test_knobs_are_mechanical_staging_fields_only(self):
        from kit import repair
        sp = json.loads((ROOT / "episodes/three-words/screenplay.json").read_text())
        for g in (dict(scene="sc4", actor="leo", other="ava", shot="sh20"), dict(scene="sc2", actor="ava", other=None, shot="sh08")):
            for k in repair.knobs(sp, g, at_scene_start=True):
                self.assertIn(k["kind"], ("point", "gap", "hug_contact"))
                if k["kind"] == "hug_contact":
                    self.assertEqual(repair.get(sp, k["path"])["do"], "hug")
                    vals = [v for v, _ in repair.candidates(sp, k)]
                    self.assertTrue(all(set(v) == {"at", "lift"} for v in vals))
                else:
                    self.assertIn(k["path"][-1], ("to", "at", "gap"), k)

    def test_candidates_are_bounded_and_nearest_first(self):
        from kit import repair
        sp = {"scenes": [{"id": "s", "cast": {"a": {"at": [0.0, 0.0]}}, "shots": []}]}
        c = list(repair.candidates(sp, dict(kind="point", path=["scenes", 0, "cast", "a", "at"])))
        self.assertEqual(len(c), len(repair.RINGS) * repair.DIRS)
        self.assertEqual([r for _, r in c], sorted(r for _, r in c))

    def test_apply_and_undo_are_exact_and_refuse_a_changed_screenplay(self):
        from kit import repair
        sp = json.loads((ROOT / "episodes/three-words/screenplay.json").read_text())
        path = ["scenes", 3, "cast", "leo", "at"]
        prop = dict(base=repair.sp_hash(sp), changes=[dict(path=path, old=repair.get(sp, path), new=[1.0, -1.0], absent=False),
                                                      dict(path=["scenes", 3, "shots", 6, "act", 3, "gap"], old=None, new=0.2, absent=True)])
        new, rec = repair.apply(sp, prop)
        self.assertEqual(repair.get(new, path), [1.0, -1.0])
        back, kept = repair.undo(new, rec)
        self.assertEqual(back, sp)
        self.assertEqual(kept, [])
        edited = json.loads(json.dumps(new))
        edited["scenes"][3]["cast"]["leo"]["at"] = [2.0, 2.0]     # a hand edit after the repair is kept
        back, kept = repair.undo(edited, rec)
        self.assertEqual(kept, [path])
        with self.assertRaises(ValueError):
            repair.apply(edited, prop)

    def test_a_start_mark_inside_furniture_is_repaired(self):
        from kit import repair
        sp = json.loads((ROOT / "episodes/three-words/screenplay.json").read_text())
        sp["scenes"][3]["cast"]["leo"] = {"at": [1.6, 1.1], "yaw": -90}    # inside the bedside cabinet
        timing = json.loads((ROOT / "episodes/three-words/voice/timing.json").read_text())
        p = repair.propose(sp, timing, max_evals=48, log=lambda m: None)
        self.assertTrue(p["ok"], p)
        self.assertEqual([c["path"] for c in p["changes"]], [["scenes", 3, "cast", "leo", "at"]])
        self.assertLessEqual(p["changes"][0]["size"], 0.8)


class HugDefaults(unittest.TestCase):
    """Hug contact defaults by height (kit/layout.py); stated values always win."""

    def test_taller_holds_high_shorter_holds_low_equal_is_left_to_the_screenplay(self):
        from kit.layout import hug_defaults, HUG_TALLER, HUG_SHORTER
        adult, child, twin = (Performer(n, scale=k) for n, k in (("a", 1.0), ("c", 0.76), ("t", 0.98)))
        self.assertEqual(hug_defaults(adult, child), HUG_TALLER)
        self.assertEqual(hug_defaults(child, adult), HUG_SHORTER)
        self.assertEqual(hug_defaults(adult, twin), {})

    def test_three_words_passes_with_every_hug_value_removed(self):
        sp = json.loads((ROOT / "episodes/three-words/screenplay.json").read_text())
        for sc in sp["scenes"]:
            for sh in sc["shots"]:
                for a in sh.get("act", []):
                    if a.get("do") == "hug":
                        for k in ("at", "lift", "lean", "head_pitch", "head_turn"):
                            a.pop(k, None)
        plan = compile_plan(sp, load_timing(ROOT / "episodes/three-words"), solve_cameras=False)
        errs = [i for i in qc.run(plan, step=2, shot_checks=False).merged() if i["severity"] == "error"]
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
