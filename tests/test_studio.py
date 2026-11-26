"""Regression tests for the studio pipeline (scripts/studio.py, scripts/video_audit.py).

    python3 tests/test_studio.py

They run in a temporary episodes/renders folder. Most need nothing but Python; the
end-to-end delivery tests also need ffmpeg (they render 4 s of synthetic picture).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import studio  # noqa: E402
import video_audit  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class Sandbox(unittest.TestCase):
    """A throwaway episodes/ and renders/ tree with one episode 'ep'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._saved = (studio.EPISODES, studio.RENDERS, studio.KIT, studio.QUALITY_PATH)
        studio.EPISODES, studio.RENDERS = base / "episodes", base / "renders"
        self.ep = studio.EPISODES / "ep"
        (self.ep / "voice/takes").mkdir(parents=True)
        self.sp = {"title": "Ep", "narrator": "a", "cast": {}, "voices": {"a": {"engine": "kokoro"}},
                   "lines": [{"id": "n01", "who": "a", "text": "Hello there."}],
                   "scenes": [{"id": "s1", "shots": [{"id": "sh01", "cam": {"size": "ms"}}, {"id": "sh02", "cam": {"size": "cu"}}]}]}
        self.write_sp()
        (self.ep / "voice/timing.json").write_text(json.dumps({"lines": {"n01": {"match": 1.0}}}))
        (self.ep / "voice/takes/n01.wav").write_bytes(b"take")
        self.out = studio.out_dir("ep", "preview")
        (self.out / "frames").mkdir(parents=True)

    def tearDown(self):
        studio.EPISODES, studio.RENDERS, studio.KIT, studio.QUALITY_PATH = self._saved
        self.tmp.cleanup()

    def write_sp(self):
        (self.ep / "screenplay.json").write_text(json.dumps(self.sp))

    def gate_code(self, fn, *args):
        with self.assertRaises(SystemExit) as cm:
            fn(*args)
        return cm.exception.code


# ---------------------------------------------------------------- fix 2: frames
class Frames(Sandbox):
    def render(self, n=5, plan="v1", only=None, since=None):
        (self.out / "plan.json").write_text(json.dumps({"frames": n, "v": plan}))
        t0 = time.time() if since is None else since
        for i in (only or range(1, n + 1)):
            (self.out / f"frames/f_{i:04d}.png").write_bytes(f"{plan}-{i}".encode())
        studio.record_frames(self.out, since=t0)

    def test_complete_render_is_deliverable(self):
        self.render()
        self.assertEqual(studio.check_frames(self.out, 5), [])

    def test_missing_frame_blocks(self):
        self.render()
        (self.out / "frames/f_0003.png").unlink()
        self.assertTrue(any("missing" in p and "0003" in p for p in studio.check_frames(self.out, 5)))

    def test_extra_frame_from_a_longer_cut_blocks(self):
        self.render(n=6)
        (self.out / "plan.json").write_text(json.dumps({"frames": 5, "v": "v1"}))
        probs = studio.check_frames(self.out, 5)
        self.assertTrue(any("extra" in p and "0006" in p for p in probs))

    def test_frames_from_an_older_plan_block(self):
        self.render()
        time.sleep(0.01)
        self.render(plan="v2", only=[2])           # partial re-render after the plan changed
        probs = studio.check_frames(self.out, 5)
        self.assertTrue(any("older plan" in p and p.startswith("4 ") for p in probs), probs)
        self.render(plan="v2")                     # full re-render fixes it
        self.assertEqual(studio.check_frames(self.out, 5), [])

    def test_frame_changed_after_rendering_blocks(self):
        self.render()
        f = self.out / "frames/f_0004.png"
        f.write_bytes(b"something else entirely")
        self.assertTrue(any("changed after rendering" in p for p in studio.check_frames(self.out, 5)))

    def test_empty_frame_blocks(self):
        self.render()
        f = self.out / "frames/f_0002.png"
        st = f.stat()
        f.write_bytes(b"")
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertTrue(any("empty" in p for p in studio.check_frames(self.out, 5)))

    def test_clean_full_render_starts_from_nothing(self):
        self.render(n=8)
        self.assertEqual(studio.prune_frames(self.out), (0, 8))
        self.assertEqual(list((self.out / "frames").iterdir()), [])

    def test_resume_keeps_only_frames_of_the_coming_plan(self):
        self.render(n=6)
        h = json.loads((self.out / "frames/manifest.json").read_text())["plan"]
        f = self.out / "frames/f_0004.png"                 # interrupted mid-frame: Blender's placeholder
        f.write_bytes(b"")
        (self.out / "frames/f_0007.png").write_bytes(b"stray")   # never recorded
        self.assertEqual(studio.prune_frames(self.out, keep_plan=h), (5, 2))
        self.assertFalse(f.exists())
        self.assertEqual(studio.prune_frames(self.out, keep_plan="another plan"), (0, 5))

    def test_partial_render_removes_its_range_so_it_is_rendered_again(self):
        self.render(n=6)
        self.assertEqual(studio.prune_frames(self.out, frame_range=(2, 3)), (4, 2))
        self.assertTrue(any("missing" in p and "0002, 0003" in p for p in studio.check_frames(self.out, 6)))

    def test_predicted_plan_matches_what_the_build_writes(self):
        """The resume logic relies on studio predicting plan.json byte for byte."""
        written = ROOT / "renders/three-words/preview/plan.json"
        if not written.exists():
            self.skipTest("no Three Words build on this machine")
        saved = studio.EPISODES
        studio.EPISODES = ROOT / "episodes"
        try:
            self.assertEqual(studio.predicted_plan_hash("three-words"), studio.fingerprint(written))
        finally:
            studio.EPISODES = saved

    def test_legacy_frames_adopted_only_if_newer_than_the_build(self):
        (self.out / "plan.json").write_text(json.dumps({"frames": 3}))
        old = time.time() - 100
        os.utime(self.out / "plan.json", (old, old))
        for i in range(1, 4):
            (self.out / f"frames/f_{i:04d}.png").write_bytes(b"x")
        self.assertEqual(studio.check_frames(self.out, 3), [])
        self.assertTrue(json.loads((self.out / "frames/manifest.json").read_text())["adopted"])

    def test_legacy_frames_older_than_the_build_are_refused(self):
        (self.out / "plan.json").write_text(json.dumps({"frames": 3}))
        for i in range(1, 4):
            f = self.out / f"frames/f_{i:04d}.png"
            f.write_bytes(b"x")
        os.utime(self.out / "frames/f_0001.png", (time.time() - 100,) * 2)   # from an earlier build
        probs = studio.check_frames(self.out, 3)
        self.assertTrue(any("unrecorded" in p for p in probs), probs)


# ---------------------------------------------------------------- fix 1: pacing
def report(**kw):
    r = dict(duration=55.0, cuts_per_minute=24, median_shot_s=2.5, longest_shot_s=4.0, static_shot_ratio=0.05,
             frozen_spans=[], frozen_seconds=0, mean_motion=3.0, first_second_motion=2.0, flash_frames=[],
             black_frames_s=[], lufs=-14.0, peak_dbfs=-1.5)
    r.update(kw)
    return r


class Pacing(unittest.TestCase):
    bar = studio.QUALITY

    def codes(self, items):
        return sorted(i["code"] for i in items)

    def test_clean_cut_passes(self):
        self.assertEqual(video_audit.judge(report(), self.bar), ([], [], []))

    def test_defects_are_hard(self):
        hard, art, _ = video_audit.judge(report(frozen_spans=[(10.0, 3.0)], flash_frames=[4.2], black_frames_s=[0.0],
                                                peak_dbfs=-0.2), self.bar)
        self.assertEqual(self.codes(hard), ["audio_peak", "flash_frames", "frozen_picture", "opens_on_black"])
        self.assertEqual(art, [])

    def test_taste_is_artistic_not_hard(self):
        hard, art, _ = video_audit.judge(report(cuts_per_minute=9, median_shot_s=5, longest_shot_s=9, static_shot_ratio=0.5,
                                                mean_motion=0.5, first_second_motion=0.1, duration=70, lufs=-17), self.bar)
        self.assertEqual(hard, [])
        self.assertEqual(self.codes(art), sorted(self.bar["pacing"]["artistic"]))

    def test_codes_match_the_quality_bar(self):
        hard, art, _ = video_audit.judge(report(frozen_spans=[(1, 3)], flash_frames=[1], black_frames_s=[0], peak_dbfs=0,
                                                cuts_per_minute=1, median_shot_s=9, longest_shot_s=9, static_shot_ratio=1,
                                                mean_motion=0, first_second_motion=0, duration=99, lufs=-30), self.bar)
        self.assertEqual(self.codes(hard), sorted(self.bar["pacing"]["hard"]))
        self.assertEqual(self.codes(art), sorted(self.bar["pacing"]["artistic"]))

    def test_declared_hold_excuses_frozen_picture(self):
        hard, _, excused = video_audit.judge(report(frozen_spans=[(10.0, 3.0)]), self.bar, holds=[(9.5, 13.2, "she cannot move")])
        self.assertEqual(hard, [])
        self.assertEqual(self.codes(excused), ["frozen_picture"])

    def test_freeze_running_past_the_hold_is_still_hard(self):
        hard, _, _ = video_audit.judge(report(frozen_spans=[(10.0, 5.0)]), self.bar, holds=[(9.5, 13.0, "held")])
        self.assertEqual(self.codes(hard), ["frozen_picture"])

    def test_holds_come_from_screenplay_shots(self):
        plan = {"shots": [{"id": "sh01", "t0": 0, "t1": 2}, {"id": "sh02", "t0": 2, "t1": 5}]}
        sp = {"scenes": [{"shots": [{"id": "sh01"}, {"id": "sh02", "hold": "the news lands"}]}]}
        self.assertEqual(video_audit.holds_from(plan, sp), [(2, 5, "the news lands")])


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class PacingOnRealVideo(unittest.TestCase):
    def test_audit_finds_the_freeze_and_the_hold_excuses_it(self):
        with tempfile.TemporaryDirectory() as d:
            mp4 = Path(d) / "freeze.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=s=144x256:r=24:d=1.5,tpad=stop_mode=clone:stop_duration=2.5",
                            "-pix_fmt", "yuv420p", str(mp4)], check=True)
            rep = video_audit.audit(mp4)
            hard, _, _ = video_audit.judge(rep, studio.QUALITY)
            self.assertIn("frozen_picture", [h["code"] for h in hard], rep["frozen_spans"])
            hard, _, excused = video_audit.judge(rep, studio.QUALITY, holds=[(1.4, 4.1, "the room goes still")])
            self.assertEqual(hard, [])
            self.assertTrue(excused)


# ---------------------------------------------------------------- end to end: invalid artifacts never deliver
@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class Delivery(Sandbox):
    """A 4 s synthetic film: 1.5 s of motion, then 2.5 s frozen (shot sh02)."""

    def setUp(self):
        super().setUp()
        plan = {"title": "Ep", "narrator": "a", "fps": 24, "duration": 4.0, "frames": 96, "lines": [],
                "shots": [{"id": "sh01", "t0": 0.0, "t1": 1.5}, {"id": "sh02", "t0": 1.5, "t1": 4.0}]}
        (self.out / "plan.json").write_text(json.dumps(plan))
        t0 = time.time()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=540x960:r=24:d=1.5,tpad=stop_mode=clone:stop_duration=2.5",
                        "-frames:v", "96", str(self.out / "frames/f_%04d.png")], check=True)
        studio.record_frames(self.out, since=t0)
        (self.out / "sound").mkdir()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=4:c=pink:a=0.1",
                        "-af", "loudnorm=I=-14:TP=-2:LRA=7,volume=0dB", "-ar", "48000", str(self.out / "sound/mix.wav")], check=True)
        studio.stamp("ep", "sound.preview", True, dict(worst_line_match=1.0))

    def deliver(self):
        studio.cmd_deliver(argparse.Namespace(slug="ep", preset="preview"))

    def test_frozen_picture_blocks_delivery(self):
        self.assertEqual(self.gate_code(self.deliver), 2)
        rec = studio.load_status("ep")["deliver.preview"]
        self.assertFalse(rec["ok"])
        self.assertIn("frozen_picture", rec["hard"])
        self.assertEqual(rec["failed"], ["no hard pacing failures"])

    def test_declared_hold_lets_the_same_film_deliver(self):
        self.sp["scenes"][0]["shots"][1]["hold"] = "the room goes still after the news"
        self.write_sp()
        studio.stamp("ep", "sound.preview", True, dict(worst_line_match=1.0))   # screenplay is a sound input
        self.deliver()
        rec = studio.load_status("ep")["deliver.preview"]
        self.assertTrue(rec["ok"], rec)
        self.assertTrue(set(rec["artistic"]) <= set(studio.QUALITY["pacing"]["artistic"]))

    def test_missing_frame_blocks_before_encoding(self):
        (self.out / "frames/f_0050.png").unlink()
        self.assertEqual(self.gate_code(self.deliver), 2)
        self.assertFalse((self.out / "ep-preview.mp4").exists())

    def test_mix_made_for_another_plan_blocks(self):
        plan = json.loads((self.out / "plan.json").read_text())
        plan["title"] = "re-cut"
        (self.out / "plan.json").write_text(json.dumps(plan))
        studio.record_frames(self.out, since=0)          # frames re-stamped for the new plan
        self.assertEqual(studio.mix_problems("ep", "preview")[0][:43], "the mix was made from different inputs (ren")
        self.assertEqual(self.gate_code(self.deliver), 2)

    def test_failed_sound_gate_blocks(self):
        studio.stamp("ep", "sound.preview", False, dict(worst_line_match=0.3))
        self.assertEqual(self.gate_code(self.deliver), 2)


# ---------------------------------------------------------------- fix 3: staleness
class Staleness(Sandbox):
    def test_upstream_artifact_change_makes_layout_stale(self):
        studio.stamp("ep", "layout", True, {})
        self.assertEqual(studio.changed_inputs("ep", "layout", studio.load_status("ep")["layout"]), [])
        (self.ep / "voice/timing.json").write_text(json.dumps({"lines": {"n01": {"match": 0.9}}}))
        self.assertEqual(studio.changed_inputs("ep", "layout", studio.load_status("ep")["layout"]), ["voice.timing"])

    def test_engine_change_makes_stages_stale(self):
        kit = Path(self.tmp.name) / "kit"
        kit.mkdir()
        (kit / "acting.py").write_text("A = 1\n")
        studio.KIT = kit
        studio.stamp("ep", "layout", True, {})
        (kit / "__pycache__").mkdir()
        (kit / "__pycache__/acting.cpython-314.pyc").write_bytes(b"cache")
        self.assertEqual(studio.changed_inputs("ep", "layout", studio.load_status("ep")["layout"]), [], "caches are not code")
        (kit / "acting.py").write_text("A = 2\n")
        self.assertEqual(studio.changed_inputs("ep", "layout", studio.load_status("ep")["layout"]), ["engine.kit"])

    def test_only_the_code_a_stage_runs_makes_it_stale(self):
        kit = Path(self.tmp.name) / "kit"
        kit.mkdir()
        for f in ("story.py", "sound.py", "camera.py", "voice.py"):
            (kit / f).write_text("x = 1\n")
        studio.KIT = kit
        (self.out / "plan.json").write_text("{}")
        for s in ("voice", "render.preview", "sound.preview"):
            studio.stamp("ep", s, True, {})
        st = lambda s: studio.changed_inputs("ep", s, studio.load_status("ep")[s])
        (kit / "story.py").write_text("x = 2\n")                  # the story gate changed
        self.assertEqual([st(s) for s in ("voice", "render.preview", "sound.preview")], [[], [], []])
        (kit / "camera.py").write_text("x = 2\n")                 # framing changed
        self.assertEqual((st("voice"), st("render.preview"), st("sound.preview")), ([], ["engine.kit"], []))
        (kit / "voice.py").write_text("x = 2\n")
        self.assertEqual(st("voice"), ["engine.kit"])

    def test_every_kit_module_belongs_to_a_stage(self):
        listed = {f for files in studio.KIT_FILES.values() for f in files}
        real = {p.name for p in (ROOT / "scripts/kit").glob("*.py")}
        self.assertEqual(real - listed, set(), "add new kit modules to studio.KIT_FILES")

    def test_configuration_change_makes_story_stale(self):
        bar = Path(self.tmp.name) / "quality-bar.json"
        bar.write_text("{}")
        studio.QUALITY_PATH = bar
        studio.stamp("ep", "story", True, {})
        bar.write_text('{"changed": 1}')
        self.assertEqual(studio.changed_inputs("ep", "story", studio.load_status("ep")["story"]), ["config.quality_bar"])

    def test_voice_ignores_camera_changes_but_not_line_changes(self):
        studio.stamp("ep", "voice", True, {})
        self.sp["scenes"][0]["shots"][0]["cam"]["size"] = "ws"
        self.write_sp()
        self.assertEqual(studio.changed_inputs("ep", "voice", studio.load_status("ep")["voice"]), [])
        self.sp["lines"][0]["text"] = "Hello again."
        self.write_sp()
        self.assertEqual(studio.changed_inputs("ep", "voice", studio.load_status("ep")["voice"]), ["screenplay.lines_and_voices"])

    def test_new_takes_make_the_mix_stale(self):
        (self.out / "plan.json").write_text("{}")
        studio.stamp("ep", "sound.preview", True, {})
        (self.ep / "voice/takes/n01.wav").write_bytes(b"new take")
        self.assertIn("assets.voice_takes", studio.changed_inputs("ep", "sound.preview", studio.load_status("ep")["sound.preview"]))

    def test_staleness_propagates_downstream(self):
        for s in ("story", "voice", "layout", "board"):
            studio.stamp("ep", s, True, {})
        self.assertEqual(studio.freshness("ep", "board"), (True, []))
        self.sp["scenes"][0]["shots"][0]["cam"]["size"] = "ws"
        self.write_sp()
        ok, why = studio.freshness("ep", "board")
        self.assertFalse(ok)
        self.assertIn("upstream layout not current", why)

    def test_stamps_from_before_input_tracking_are_stale(self):
        (studio.reports("ep") / "status.json").write_text(json.dumps({"story": {"ok": True, "at": "2026-11-26 12:00:00"}}))
        ok, why = studio.freshness("ep", "story")
        self.assertFalse(ok)
        self.assertIn("no input record", why[0])


# ---------------------------------------------------------------- fix 5 + review checkpoint
class Reviews(Sandbox):
    SAW = "sh01 to sh22 read with the sound off; the turn at sh09 lands; sh14 two-shot is cramped but readable"

    def setUp(self):
        super().setUp()
        self.board = studio.out_dir("ep", "board")
        self.board.mkdir(parents=True)
        (self.board / "storyboard.jpg").write_bytes(b"sheet v1")

    def review(self, kind="board", verdict="approve", saw=SAW, notes=None):
        studio.cmd_review(argparse.Namespace(slug="ep", kind=kind, preset="preview", verdict=verdict, saw=saw,
                                             note=notes, reviewer="director"))

    def test_no_review_blocks_a_full_render(self):
        for s in ("story", "voice", "layout", "board"):
            studio.stamp("ep", s, True, {})
        code = self.gate_code(studio.cmd_render, argparse.Namespace(slug="ep", preset="preview", frames=None, unreviewed=None))
        self.assertEqual(code, 3)

    def test_a_one_line_review_is_refused(self):
        with self.assertRaises(SystemExit):
            self.review(saw="looks fine")
        self.assertEqual(studio.load_reviews("ep"), [])

    def test_approval_is_tied_to_the_pictures_reviewed(self):
        self.review()
        self.assertTrue(studio.review_status("ep", "board")[0])
        self.assertTrue((self.ep / "reviews.json").exists())
        (self.board / "storyboard.jpg").write_bytes(b"sheet v2")
        ok, why = studio.review_status("ep", "board")
        self.assertFalse(ok)
        self.assertIn("changed after the review", why)

    def test_revise_verdict_does_not_approve(self):
        self.assertEqual(self.gate_code(self.review, "board", "revise"), 2)
        self.assertFalse(studio.review_status("ep", "board")[0])

    def picture_ready(self, artistic):
        (self.out / "ep-preview.mp4").write_bytes(b"movie")
        (self.out / "frames.jpg").write_bytes(b"sheet")
        studio.stamp("ep", "deliver.preview", True, {})
        (studio.reports("ep") / "pacing.preview.json").write_text(json.dumps({"artistic": [{"code": c, "msg": c} for c in artistic]}))

    def test_picture_approval_needs_a_disposition_per_artistic_note(self):
        self.picture_ready(["first_second_motion", "duration"])
        with self.assertRaises(SystemExit):
            self.review("picture", notes=["first_second_motion=the opening is a held photo, on purpose"])
        self.review("picture", notes=["first_second_motion=the opening is a held photo, on purpose",
                                      "duration=66.9 s; the button line needs the extra beat"])
        self.assertTrue(studio.review_status("ep", "picture", "preview")[0])

    def test_picture_review_needs_a_passed_delivery(self):
        (self.out / "ep-preview.mp4").write_bytes(b"movie")
        (self.out / "frames.jpg").write_bytes(b"sheet")
        studio.stamp("ep", "deliver.preview", False, {})
        with self.assertRaises(SystemExit):
            self.review("picture")


class Make(Sandbox):
    def run_make(self, board_fresh, reviewed):
        calls = []
        saved = {k: getattr(studio, k) for k in ("cmd_story", "cmd_voice", "cmd_layout", "cmd_board", "cmd_render",
                                                  "cmd_sound", "cmd_deliver", "freshness", "review_status")}
        for k in ("story", "voice", "layout", "board", "render", "sound", "deliver"):
            setattr(studio, "cmd_" + k, lambda a, k=k: calls.append(k))
        studio.freshness = lambda slug, stage, *x: (board_fresh, [])
        studio.review_status = lambda slug, kind, preset=None: (reviewed, "approved" if reviewed else "no review recorded")
        (studio.out_dir("ep", "board")).mkdir(parents=True, exist_ok=True)
        (studio.out_dir("ep", "board") / "storyboard.jpg").write_bytes(b"x")
        try:
            studio.cmd_make(argparse.Namespace(slug="ep", preset="preview"))
            return calls, 0
        except SystemExit as e:
            return calls, e.code
        finally:
            for k, v in saved.items():
                setattr(studio, k, v)

    def test_make_builds_the_board_and_stops_for_review(self):
        calls, code = self.run_make(board_fresh=False, reviewed=False)
        self.assertEqual(calls, ["story", "voice", "layout", "board"])
        self.assertEqual(code, 3)

    def test_make_keeps_a_current_board_and_continues_after_approval(self):
        calls, code = self.run_make(board_fresh=True, reviewed=True)
        self.assertEqual(calls, ["story", "voice", "layout", "render", "sound", "deliver"])
        self.assertEqual(code, 0)


# ---------------------------------------------------------------- retrospective
class Retrospective(Sandbox):
    def test_new_episode_gets_a_retrospective_and_retro_measures_it(self):
        studio.cmd_new(argparse.Namespace(slug="fresh", title="Fresh"))
        p = studio.ep_dir("fresh") / "retrospective.md"
        self.assertIn("Produced without modifying the core engine: TODO", p.read_text())
        self.assertEqual(studio.retro_state("fresh"), "verdict not answered")
        studio.stamp("fresh", "layout", False, dict(errors=3, error_rules=["collide.char"]))
        studio.stamp("fresh", "layout", True, dict(errors=0))
        studio.manual("fresh", "render.preview without a current board review", "testing lights")
        p.write_text(p.read_text().replace("Produced without modifying the core engine: TODO (yes / no)",
                                           "Produced without modifying the core engine: yes"))
        studio.cmd_retro(argparse.Namespace(slug="fresh"))
        text = p.read_text()
        self.assertIn("Quality-control failures: 1", text)
        self.assertIn("collide.char", text)
        self.assertIn("testing lights", text)
        self.assertIn("| layout | 2 | 1 | 1 |", text)
        self.assertEqual(studio.retro_state("fresh"), "answered (yes)")
        studio.cmd_retro(argparse.Namespace(slug="fresh"))       # refreshing keeps the written part
        self.assertEqual(p.read_text().count("## Measured"), 1)
        self.assertIn("Produced without modifying the core engine: yes", p.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
