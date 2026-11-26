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


if __name__ == "__main__":
    unittest.main(verbosity=2)
