"""Listening check (runs in .audio-tools/asr): can each line be understood through the full mix?

Prints JSON {worst, lines: {id: {match, heard}}}. Numbers are spelled out before comparing,
so "10 years" matches "ten years".
"""

import difflib
import json
import re
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel

NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "10": "ten"}


def norm(s):
    s = s.lower().replace("’", "'")
    toks = re.findall(r"[a-z0-9']+", s)
    out = []
    for t in toks:
        t = NUM.get(t, t)
        out += {"who's": ["who", "is"], "that's": ["that", "is"], "it's": ["it", "is"], "he'd": ["he", "had"]}.get(t, [t])
    return out


def main():
    plan = json.load(open(sys.argv[1]))
    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", sys.argv[2], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                                       capture_output=True, check=True).stdout, "<f4")
    model = WhisperModel("small.en", device="cpu", compute_type="int8", local_files_only=True)
    res, worst = {}, 1.0
    for L in plan["lines"]:
        a, b = max(0, int((L["t0"] - 0.1) * 16000)), int((L["t1"] + 0.25) * 16000)
        segs, _ = model.transcribe(pcm[a:b].copy(), language="en", beam_size=5)
        heard = " ".join(s.text.strip() for s in segs)
        r = difflib.SequenceMatcher(a=norm(L["text"]), b=norm(heard)).ratio()
        worst = min(worst, r)
        res[L["id"]] = dict(match=round(r, 3), heard=heard)
    print(json.dumps(dict(worst=round(worst, 3), lines=res)))


if __name__ == "__main__":
    main()
