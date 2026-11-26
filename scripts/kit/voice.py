"""Voice department: synthesise each screenplay line offline, then measure it.

Run with the audio venv (numpy + kokoro):
    .audio-tools/venv/bin/python scripts/kit/voice.py episodes/<slug> [--force ID ...]

For every line it writes voice/takes/<id>.wav (48 kHz mono, trimmed, levelled),
then runs word alignment (faster-whisper, local) and writes voice/timing.json with
duration, word timings, an intelligibility check and a 24 fps mouth envelope.
Takes are cached by text + voice settings, so re-running only redoes what changed.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".audio-tools"
RATE = 48000


def ff(args, **kw):
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, **kw)


def decode(path, rate=RATE):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                       check=True, capture_output=True)
    return np.frombuffer(r.stdout, "<f4").copy()


def write_wav(path, pcm, rate=RATE):
    ff(["-f", "f32le", "-ar", rate, "-ac", 1, "-i", "-", "-c:a", "pcm_s16le", path], input=pcm.astype("<f4").tobytes())


def synth_qwen(text, spec, out):
    cmd = [TOOLS / "bin/crispasr", "--backend", "qwen3-tts",
           "-m", TOOLS / "models/qwen3-tts-12hz-1.7b-customvoice-q8_0.gguf",
           "--codec-model", TOOLS / "models/qwen3-tts-tokenizer-12hz.gguf",
           "--voice", spec.get("speaker", "ryan"), "--seed", str(spec.get("seed", 197)),
           "-t", str(spec.get("threads", 10)), "--no-gpu", "--tts", text, "--tts-output", out]
    if spec.get("instruct"):
        cmd[cmd.index("--seed"):cmd.index("--seed")] = ["--instruct", spec["instruct"]]
    env = dict(os.environ, OMP_NUM_THREADS="10")
    r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True, env=env, timeout=600)
    if r.returncode or not Path(out).exists():
        raise RuntimeError(f"qwen3-tts failed for '{text}': {r.stderr[-800:]}")


def synth_kokoro(text, spec, out):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 8
    sess = ort.InferenceSession(str(TOOLS / "models/kokoro-v1.0.int8.onnx"), opts, providers=["CPUExecutionProvider"])
    k = Kokoro.from_session(sess, str(TOOLS / "models/voices-v1.0.bin"))
    pcm, rate = k.create(text, voice=spec.get("speaker", "af_heart"), lang="en-us", speed=spec.get("speed", 1.0))
    ff(["-f", "f32le", "-ar", rate, "-ac", 1, "-i", "-", out], input=np.asarray(pcm, "<f4").tobytes())


def trim(pcm, thresh_db=-42, pad=0.05):
    win = int(RATE * 0.01)
    n = len(pcm) // win
    if n == 0:
        return pcm
    rms = np.sqrt(np.mean(pcm[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    idx = np.where(db > thresh_db)[0]
    if len(idx) == 0:
        return pcm
    a = max(0, idx[0] * win - int(pad * RATE))
    b = min(len(pcm), (idx[-1] + 1) * win + int(pad * 1.6 * RATE))
    out = pcm[a:b].copy()
    fade = int(0.012 * RATE)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out


def level(pcm, target_db=-20.0):
    """Level a dialogue take by active-speech RMS (simple, stable for short lines)."""
    win = int(RATE * 0.02)
    n = len(pcm) // win
    rms = np.sqrt(np.mean(pcm[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    active = rms[rms > rms.max() * 0.1]
    cur = 20 * np.log10(np.sqrt(np.mean(active ** 2)) + 1e-12)
    g = 10 ** ((target_db - cur) / 20)
    out = pcm * g
    peak = np.abs(out).max()
    if peak > 0.89:
        out *= 0.89 / peak
    return out


def envelope(pcm, fps=24):
    hop = RATE // fps
    n = int(np.ceil(len(pcm) / hop))
    pad = np.pad(pcm, (0, n * hop - len(pcm)))
    rms = np.sqrt(np.mean(pad.reshape(n, hop) ** 2, axis=1))
    e = rms / (np.percentile(rms, 95) + 1e-9)
    e = np.clip(e, 0, 1)
    # Light smoothing; mouths look better slightly ahead of the sound.
    e = np.convolve(e, [0.25, 0.5, 0.25], mode="same")
    e = np.concatenate([e[1:], e[-1:]])
    return [round(float(x), 3) for x in e]


def take_key(text, spec):
    return hashlib.sha1(json.dumps([text, spec], sort_keys=True).encode()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--force", nargs="*", default=[])
    ap.add_argument("--no-align", action="store_true")
    a = ap.parse_args()
    ep = Path(a.episode)
    sp = json.loads((ep / "screenplay.json").read_text())
    voices = sp.get("voices", {})
    vdir = ep / "voice"
    takes = vdir / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    manifest_p = vdir / "manifest.json"
    manifest = json.loads(manifest_p.read_text()) if manifest_p.exists() else {}
    for L in sp["lines"]:
        vid = L.get("voice", L["who"])
        if vid not in voices:
            raise SystemExit(f"line {L['id']}: no voice '{vid}' in screenplay voices")
        spec = voices[vid]
        key = take_key(L.get("say", L["text"]), spec)
        out = takes / f"{L['id']}.wav"
        if out.exists() and manifest.get(L["id"], {}).get("key") == key and L["id"] not in a.force:
            continue
        print(f"[voice] {L['id']} ({vid}, {spec['engine']}): {L['text']}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.wav"
            if spec["engine"] == "qwen3":
                synth_qwen(L.get("say", L["text"]), spec, raw)
            elif spec["engine"] == "kokoro":
                synth_kokoro(L.get("say", L["text"]), spec, raw)
            else:
                raise SystemExit(f"unknown engine {spec['engine']}")
            filt = []
            if spec.get("pitch") or spec.get("tempo"):
                ratio = 2 ** (spec.get("pitch", 0.0) / 12)
                filt.append(f"rubberband=pitch={ratio:.4f}:tempo={spec.get('tempo', 1.0):.3f}:formant=preserved")
            filt.append("highpass=f=70")
            proc = Path(tmp) / "proc.wav"
            ff(["-i", raw, "-af", ",".join(filt), "-ac", 1, "-ar", RATE, proc])
            pcm = level(trim(decode(proc)))
            write_wav(out, pcm)
        manifest[L["id"]] = dict(key=key, voice=vid, engine=spec["engine"], speaker=spec.get("speaker"), text=L["text"])
        manifest_p.write_text(json.dumps(manifest, indent=1))

    timing = {"lines": {}}
    for L in sp["lines"]:
        out = takes / f"{L['id']}.wav"
        pcm = decode(out)
        timing["lines"][L["id"]] = dict(file=str(out.relative_to(ep)), dur=round(len(pcm) / RATE, 4),
                                        env=envelope(pcm), env_fps=24)
    if not a.no_align:
        r = subprocess.run([str(TOOLS / "asr/bin/python"), str(Path(__file__).with_name("align.py")), str(ep)],
                           capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-2000:], file=sys.stderr)
            raise SystemExit("alignment failed")
        words = json.loads(r.stdout)
        for lid, info in words.items():
            timing["lines"][lid].update(words=info["words"], heard=info["heard"], match=info["match"])
    (vdir / "timing.json").write_text(json.dumps(timing, indent=1))
    bad = [(k, v.get("heard")) for k, v in timing["lines"].items() if v.get("match", 1) < 0.8]
    total = sum(v["dur"] for v in timing["lines"].values())
    print(f"[voice] {len(timing['lines'])} lines, {total:.1f}s of speech")
    for k, h in bad:
        print(f"[voice] WARNING {k}: ASR heard '{h}' (check pronunciation)")


if __name__ == "__main__":
    main()
