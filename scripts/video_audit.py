"""Measure how alive a finished cut is: cuts, motion, frozen stretches, flashes, silence.

Host-side and dependency-free (FFmpeg only). It answers "does this read as a
slideshow or as animation?" with numbers that can be compared with a reference
reel or with studio/quality-bar.json.

    python3 scripts/video_audit.py MOVIE [--bar studio/quality-bar.json] [--json OUT] [--strict]
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, den = map(int, video["avg_frame_rate"].split("/"))
    return {"width": video["width"], "height": video["height"], "fps": num / den,
            "duration": float(data["format"]["duration"]), "has_audio": audio is not None}


def motion_series(path, fps):
    """Mean absolute luma change per frame, from a 72x128 difference stream."""
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an",
               "-vf", "scale=72:128:flags=area,format=gray,tblend=all_mode=difference,signalstats,"
                      "metadata=print:key=lavfi.signalstats.YAVG:file=-",
               "-f", "null", "-"]
    out = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    values = [float(m) for m in re.findall(r"YAVG=([0-9.]+)", out)]
    return values[1:] if values else values


def luma_series(path):
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an",
               "-vf", "scale=72:128:flags=area,format=gray,signalstats,"
                      "metadata=print:key=lavfi.signalstats.YAVG:file=-",
               "-f", "null", "-"]
    out = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return [float(m) for m in re.findall(r"YAVG=([0-9.]+)", out)]


def cut_times(motion, fps):
    """A cut is an isolated spike far above the local motion floor."""
    cuts = []
    for i, value in enumerate(motion):
        window = motion[max(0, i - 6):i] + motion[i + 1:i + 7]
        if not window:
            continue
        floor = statistics.median(window)
        if value > max(9.0, floor * 5):
            if not cuts or (i + 1) / fps - cuts[-1] > 0.25:
                cuts.append((i + 1) / fps)
    return cuts


def silences(path, floor_db=-50, minimum=1.2):
    out = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-i", str(path), "-vn",
                          "-af", f"silencedetect=noise={floor_db}dB:d={minimum}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", out)]
    return [(s, e) for s, e in zip(starts, ends + [None] * (len(starts) - len(ends)))]


def loudness(path):
    out = subprocess.run(["ffmpeg", "-nostdin", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = out[out.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?[0-9.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[0-9.]+) dBFS", summary)
    return (float(lufs.group(1)) if lufs else None, float(peak.group(1)) if peak else None)


def runs_below(values, limit, fps, minimum):
    """Spans (start_s, length_s) where motion stays under limit for at least minimum seconds."""
    spans, begin = [], None
    for i, v in enumerate(values + [limit + 1]):
        if v < limit and begin is None:
            begin = i
        elif v >= limit and begin is not None:
            length = (i - begin) / fps
            if length >= minimum:
                spans.append((round((begin + 1) / fps, 2), round(length, 2)))
            begin = None
    return spans


def audit(path):
    info = ffprobe(path)
    fps = info["fps"]
    motion = motion_series(path, fps)
    luma = luma_series(path)
    cuts = cut_times(motion, fps)
    edges = [0.0, *cuts, info["duration"]]
    shots = [{"start": round(a, 2), "length": round(b - a, 2)} for a, b in zip(edges, edges[1:]) if b - a > 0.2]
    for shot in shots:
        a, b = round(shot["start"] * fps), round((shot["start"] + shot["length"]) * fps)
        body = [v for i, v in enumerate(motion[a:b]) if (a + i + 1) / fps - shot["start"] > 0.1
                and not any(abs((a + i + 1) / fps - c) < 0.1 for c in cuts)]
        shot["motion"] = round(statistics.mean(body), 3) if body else 0.0
    lengths = [s["length"] for s in shots]
    flashes = [round((i + 1) / fps, 2) for i in range(1, len(luma) - 1)
               if abs(luma[i] - luma[i - 1]) > 55 and abs(luma[i] - luma[i + 1]) > 55]
    dark = [round(i / fps, 2) for i, v in enumerate(luma) if v < 3]
    quiet = runs_below(motion, 0.05, fps, 0.9)
    report = {
        "movie": str(path), **info,
        "shots": len(shots),
        "cuts_per_minute": round(len(cuts) / info["duration"] * 60, 1),
        "median_shot_s": round(statistics.median(lengths), 2) if lengths else info["duration"],
        "longest_shot_s": max(lengths) if lengths else info["duration"],
        "mean_motion": round(statistics.mean(motion), 3) if motion else 0,
        "static_shot_ratio": round(sum(1 for s in shots if s["motion"] < 0.18) / max(1, len(shots)), 2),
        "frozen_spans": quiet,
        "frozen_seconds": round(sum(l for _, l in quiet), 1),
        "first_second_motion": round(statistics.mean(motion[2:int(fps)]), 3) if len(motion) > fps else 0,
        "flash_frames": flashes,
        "black_frames_s": dark[:8],
        "shot_list": shots,
    }
    if info["has_audio"]:
        gaps = silences(path)
        lufs, peak = loudness(path)
        report.update(silent_gaps=[(round(s, 2), round(e, 2) if e else None) for s, e in gaps],
                      lufs=lufs, peak_dbfs=peak)
    return report


def judge(report, bar):
    """Compare a report with the house bar. Returns (failures, warnings)."""
    fails, warns = [], []
    limits = bar["video"]
    if report["cuts_per_minute"] < limits["min_cuts_per_minute"]:
        fails.append(f"only {report['cuts_per_minute']} cuts/min, bar is {limits['min_cuts_per_minute']}")
    if report["median_shot_s"] > limits["max_median_shot_s"]:
        fails.append(f"median shot {report['median_shot_s']}s exceeds {limits['max_median_shot_s']}s")
    if report["longest_shot_s"] > limits["max_longest_shot_s"]:
        warns.append(f"longest shot {report['longest_shot_s']}s exceeds {limits['max_longest_shot_s']}s")
    if report["static_shot_ratio"] > limits["max_static_shot_ratio"]:
        fails.append(f"{int(report['static_shot_ratio'] * 100)}% of shots are nearly motionless")
    if report["frozen_seconds"] > limits["max_frozen_seconds"]:
        fails.append(f"{report['frozen_seconds']}s of frozen picture: {report['frozen_spans'][:4]}")
    if report["mean_motion"] < limits.get("min_mean_motion", 0):
        warns.append(f"mean motion {report['mean_motion']} is below {limits['min_mean_motion']}; the picture reads as stills")
    if report["first_second_motion"] < limits["min_first_second_motion"]:
        warns.append("first second barely moves; the hook needs motion or a strong still image")
    if report["flash_frames"]:
        fails.append(f"single-frame brightness flashes at {report['flash_frames'][:4]}")
    if report["black_frames_s"] and report["black_frames_s"][0] < 0.2:
        fails.append("opens on black")
    if not limits["min_seconds"] <= report["duration"] <= limits["max_seconds"]:
        warns.append(f"duration {report['duration']:.1f}s is outside {limits['min_seconds']}-{limits['max_seconds']}s")
    if report.get("lufs") is not None and not limits["lufs"][0] <= report["lufs"] <= limits["lufs"][1]:
        warns.append(f"loudness {report['lufs']} LUFS is outside {limits['lufs']}")
    if report.get("peak_dbfs") is not None and report["peak_dbfs"] > limits["max_peak_dbfs"]:
        fails.append(f"peak {report['peak_dbfs']} dBFS is above {limits['max_peak_dbfs']}")
    return fails, warns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("movie", type=Path)
    parser.add_argument("--bar", type=Path, default=ROOT / "studio/quality-bar.json")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--strict", action="store_true", help="exit nonzero on any failure")
    parser.add_argument("--shots", action="store_true", help="print the per-shot table")
    args = parser.parse_args()
    report = audit(args.movie)
    summary = {k: v for k, v in report.items() if k != "shot_list"}
    print(json.dumps(summary, indent=1))
    if args.shots:
        for shot in report["shot_list"]:
            print(f"  {shot['start']:7.2f}s  {shot['length']:5.2f}s  motion {shot['motion']:.2f}")
    if args.bar.is_file():
        fails, warns = judge(report, json.loads(args.bar.read_text()))
        report["fails"], report["warnings"] = fails, warns
        for line in fails:
            print("FAIL", line)
        for line in warns:
            print("WARN", line)
        if not fails:
            print("PASS video bar")
        if args.strict and fails:
            sys.exit(1)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
