"""Word timings and an intelligibility check for every take (runs in .audio-tools/asr).

Prints JSON {line_id: {words: [{w, s, e}], heard, match}} to stdout. Word times come
from faster-whisper; the script's own words are mapped onto them so captions always
show the written text even when the recogniser mishears a name.
"""

import difflib
import json
import re
import sys
from pathlib import Path

import subprocess

import numpy as np
from faster_whisper import WhisperModel


def load16k(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       check=True, capture_output=True)
    return np.frombuffer(r.stdout, "<f4").copy()


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def main():
    ep = Path(sys.argv[1])
    sp = json.loads((ep / "screenplay.json").read_text())
    model = WhisperModel("small.en", device="cpu", compute_type="int8", local_files_only=True)
    out = {}
    for L in sp["lines"]:
        wav = ep / "voice" / "takes" / f"{L['id']}.wav"
        segs, _ = model.transcribe(load16k(wav), word_timestamps=True, beam_size=5, language="en",
                                   initial_prompt=L["text"] if L.get("prompt_asr") else None)
        heard = []
        for s in segs:
            for w in s.words:
                heard.append((w.word.strip(), w.start, w.end))
        script = re.findall(r"[\w'’-]+[.,!?…]*", L["text"])
        spoken = [norm(x) for x in re.findall(r"[\w'’-]+", L.get("say", L["text"]))]
        hs = [norm(h[0]) for h in heard]
        ss = [norm(x) for x in script]
        sm = difflib.SequenceMatcher(a=ss, b=hs)
        times = [None] * len(script)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    times[i1 + k] = heard[j1 + k][1:]
            elif tag == "replace" and (i2 - i1) == (j2 - j1):
                for k in range(i2 - i1):
                    times[i1 + k] = heard[j1 + k][1:]
            elif tag == "replace" and j2 > j1:
                s0, e0 = heard[j1][1], heard[j2 - 1][2]
                n = i2 - i1
                for k in range(n):
                    times[i1 + k] = (s0 + (e0 - s0) * k / n, s0 + (e0 - s0) * (k + 1) / n)
        # Fill gaps by interpolation.
        dur = heard[-1][2] if heard else 1.0
        for i, t in enumerate(times):
            if t is None:
                prev = next((times[j][1] for j in range(i - 1, -1, -1) if times[j]), 0.0)
                nxt = next((times[j][0] for j in range(i + 1, len(times)) if times[j]), dur)
                times[i] = (prev, max(prev + 0.08, (prev + nxt) / 2))
        out[L["id"]] = dict(
            words=[dict(w=w, s=round(t[0], 3), e=round(t[1], 3)) for w, t in zip(script, times)],
            heard=" ".join(h[0] for h in heard),
            match=round(difflib.SequenceMatcher(a=spoken, b=hs).ratio(), 3),
        )
    print(json.dumps(out))


if __name__ == "__main__":
    main()
