"""Sound department: ambience, foley, score and mix, all generated locally.

Run with the audio venv after a build has written plan.json:
    .audio-tools/venv/bin/python scripts/kit/sound.py EPISODE_DIR RENDER_DIR

Inputs: RENDER_DIR/plan.json (shots, lines, animation events, music cues) and the
voice takes. Outputs in RENDER_DIR/sound/: stems (dialogue, ambience, fx, music),
premix.wav and mix.wav (-14 LUFS, -1.5 dBTP), plus cues.json listing every sound
with its frame so a reviewer can check sync.

Foley is spotted from the animation's own events (footfalls, hug cloth, counter
ticks, the invisible-wall ripple), so contact sounds land on the frame by construction.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

RATE = 48000
rng = np.random.default_rng(7)


# ------------------------------------------------------------------ dsp helpers
def t_axis(n):
    return np.arange(n) / RATE


def onepole_lp(x, fc):
    a = math.exp(-2 * math.pi * fc / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # short buffers only
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def fft_filter(x, lo=None, hi=None):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    g = np.ones_like(f)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1e-3)) ** 4)
    if hi:
        g *= 1 / np.sqrt(1 + (f / hi) ** 4)
    return np.fft.irfft(X * g, len(x))


def noise(n, color="white"):
    w = rng.standard_normal(n)
    if color == "pink":
        X = np.fft.rfft(w)
        f = np.fft.rfftfreq(n, 1 / RATE)
        X /= np.sqrt(np.maximum(f, 20))
        w = np.fft.irfft(X, n)
    elif color == "brown":
        w = np.cumsum(w)
        w -= np.convolve(w, np.ones(4801) / 4801, mode="same")
    return w / (np.abs(w).max() + 1e-9)


def env_adsr(n, a=0.005, d=0.1, s=0.0, r=0.05):
    t = t_axis(n)
    e = np.minimum(1, t / max(a, 1e-4))
    dec = np.exp(-np.maximum(0, t - a) / max(d, 1e-4))
    return e * (s + (1 - s) * dec)


def reverb_ir(seconds=1.8, damp=6000, pre=0.012):
    n = int(seconds * RATE)
    t = t_axis(n)
    ir = rng.standard_normal((2, n)) * np.exp(-t * 6.9 / seconds)
    for c in range(2):
        ir[c] = fft_filter(ir[c], hi=damp)
    pad = int(pre * RATE)
    ir = np.concatenate([np.zeros((2, pad)), ir], axis=1)
    return ir / np.sqrt(np.sum(ir ** 2) / 2)


def convolve_stereo(x, ir, wet=0.25):
    """x: (2, n). FFT convolution with a stereo IR."""
    n = x.shape[1] + ir.shape[1]
    N = 1 << (n - 1).bit_length()
    out = np.zeros((2, x.shape[1]))
    for c in range(2):
        y = np.fft.irfft(np.fft.rfft(x[c], N) * np.fft.rfft(ir[c], N), N)[: x.shape[1]]
        out[c] = x[c] * (1 - wet) + y * wet
    return out


def place(buf, sig, t, gain=1.0, pan=0.0):
    """Add mono or stereo signal into a (2, n) buffer at time t (seconds)."""
    i = int(round(t * RATE))
    if i >= buf.shape[1]:
        return
    if sig.ndim == 1:
        l = math.cos((pan + 1) * math.pi / 4)
        r = math.sin((pan + 1) * math.pi / 4)
        sig = np.vstack([sig * l * 1.414, sig * r * 1.414])
    j = min(buf.shape[1], i + sig.shape[1])
    if i < 0:
        sig = sig[:, -i:]
        i = 0
    buf[:, i:j] += sig[:, : j - i] * gain


def db(x):
    return 10 ** (x / 20)


def decode(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                       check=True, capture_output=True)
    return np.frombuffer(r.stdout, "<f4").copy()


def write(path, x):
    x = np.clip(x, -1, 1)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", str(x.shape[0]), "-i", "-",
                    "-c:a", "pcm_s24le", str(path)], input=x.T.astype("<f4").tobytes(), check=True)


# ------------------------------------------------------------------ foley
def step(surface, run=False, scale=1.0):
    n = int(0.16 * RATE)
    t = t_axis(n)
    body = {"concrete": (95, 0.02), "wood": (120, 0.05), "vinyl": (140, 0.025)}.get(surface, (110, 0.03))
    thump = np.sin(2 * math.pi * body[0] / max(scale, 0.6) * t) * np.exp(-t / body[1])
    grit = fft_filter(noise(n), lo=900 if surface != "vinyl" else 1500, hi=6000 if surface == "concrete" else 4000) * np.exp(-t / 0.018)
    x = thump * 0.7 + grit * (0.55 if surface == "concrete" else 0.3)
    if surface == "vinyl":
        x += 0.12 * np.sin(2 * math.pi * 2400 * t) * np.exp(-t / 0.01) * rng.uniform(0.3, 1)
    x *= (1.5 if run else 1.0) * rng.uniform(0.75, 1.0) * (0.6 + 0.4 * scale)
    return x


def cloth(dur=0.6):
    n = int(dur * RATE)
    t = t_axis(n)
    e = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2
    return fft_filter(noise(n, "pink"), lo=300, hi=3500) * e * 0.5


def chime(kind="tick"):
    n = int(1.6 * RATE)
    t = t_axis(n)
    if kind == "reveal":
        freqs = [1046.5, 1318.5, 1568.0, 2093.0]
        x = sum(np.sin(2 * math.pi * f * t + i) * np.exp(-t / (0.9 - 0.12 * i)) * (0.5 ** i) for i, f in enumerate(freqs))
        sweep = np.sin(2 * math.pi * (600 * t + 900 * t * t)) * np.exp(-t / 0.25) * 0.15
        return (x * 0.35 + sweep) * np.minimum(1, t / 0.01)
    freqs = [1567.98, 2349.3]
    x = sum(np.sin(2 * math.pi * f * t) * np.exp(-t / 0.35) * (0.6 ** i) for i, f in enumerate(freqs))
    return x * 0.3 * np.minimum(1, t / 0.004)


def ripple():
    n = int(1.2 * RATE)
    t = t_axis(n)
    f = 220 + 520 * np.exp(-t / 0.18)
    ph = np.cumsum(2 * math.pi * f / RATE)
    x = np.sin(ph) * np.exp(-t / 0.35) * 0.45
    x += sum(np.sin(2 * math.pi * fr * t) * np.exp(-t / 0.6) * 0.08 for fr in (1760, 2217, 2637))
    thud = np.sin(2 * math.pi * 70 * t) * np.exp(-t / 0.08) * 0.5
    return x + thud


def sit_sound():
    n = int(0.4 * RATE)
    t = t_axis(n)
    return (np.sin(2 * math.pi * 85 * t) * np.exp(-t / 0.06) * 0.5 + fft_filter(noise(n, "pink"), lo=200, hi=2500) * np.exp(-t / 0.12) * 0.3)


# ------------------------------------------------------------------ ambience
def ambience(kind, n, seed=0):
    r = np.random.default_rng(seed)
    t = t_axis(n)
    out = np.zeros((2, n))
    if kind == "schoolyard":
        for c in range(2):
            out[c] += fft_filter(noise(n, "pink"), lo=120, hi=1800) * 0.05
            chatter = fft_filter(noise(n), lo=300, hi=2600)
            mod = fft_filter(r.random(n) ** 6, hi=6) * 40
            out[c] += chatter * np.clip(mod, 0, 1) * 0.06
        for k in range(int(n / RATE * 0.7)):
            t0 = r.uniform(0, n / RATE - 0.5)
            m = int(0.25 * RATE)
            tt = t_axis(m)
            f0 = r.uniform(2800, 4200)
            chirp = np.sin(2 * math.pi * (f0 * tt + r.uniform(-3000, 3000) * tt * tt)) * np.sin(np.pi * tt / 0.25) ** 2 * 0.05
            place(out, chirp, t0, pan=r.uniform(-0.8, 0.8))
    elif kind == "living_room":
        tone = fft_filter(noise(n, "brown"), hi=400) * 0.05
        out += tone
        for k in range(int(n / RATE)):
            m = int(0.03 * RATE)
            tick = fft_filter(noise(m), lo=2000) * np.exp(-t_axis(m) / 0.004) * (0.05 if k % 2 else 0.035)
            place(out, tick, k + 0.37, pan=-0.4)
        for c in range(2):
            out[c] += fft_filter(noise(n, "pink"), lo=1500, hi=5000) * 0.006
    elif kind == "hospital_room":
        hum = sum(np.sin(2 * math.pi * f * t) * a for f, a in ((60, 0.02), (120, 0.012), (180, 0.006)))
        out += hum
        for c in range(2):
            out[c] += fft_filter(noise(n, "pink"), lo=80, hi=900) * 0.035
        period = 0.92
        k = 0
        while k * period < n / RATE:
            m = int(0.11 * RATE)
            tt = t_axis(m)
            beep = np.sin(2 * math.pi * 987.8 * tt) * np.minimum(1, tt / 0.004) * np.clip((0.11 - tt) / 0.02, 0, 1) * 0.06
            place(out, beep, k * period + 0.2, pan=0.5)
            k += 1
    elif kind == "corridor":
        buzz = np.sign(np.sin(2 * math.pi * 120 * t)) * 0.004 + np.sin(2 * math.pi * 120 * t) * 0.008
        out += buzz
        for c in range(2):
            out[c] += fft_filter(noise(n, "pink"), lo=60, hi=1200) * 0.045
            out[c] += fft_filter(noise(n), lo=400, hi=1800) * np.clip(fft_filter(r.random(n) ** 8, hi=3) * 30, 0, 1) * 0.02
    return out


# ------------------------------------------------------------------ score
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def midi(name):
    pitch, octv = name[:-1], int(name[-1])
    return 12 * (octv + 1) + NOTE[pitch]


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


def piano(m, dur, vel=0.6):
    f = hz(m)
    n = int((dur + 1.8) * RATE)
    t = t_axis(n)
    x = np.zeros(n)
    B = 0.0004
    for k in range(1, 9):
        fk = f * k * math.sqrt(1 + B * k * k)
        if fk > 12000:
            break
        decay = (2.8 - 0.18 * k) * (220 / f) ** 0.35
        x += np.sin(2 * math.pi * fk * t + k) * np.exp(-t / max(decay, 0.15)) * (0.55 ** (k - 1)) * (1 + 0.6 * vel * (k > 2))
    x *= np.minimum(1, t / 0.003)
    rel = np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    x *= np.maximum(rel, 0) ** 1.5 + (1 - np.maximum(rel, 0)) * 0
    hammer = fft_filter(noise(int(0.02 * RATE)), lo=1500) * np.exp(-t_axis(int(0.02 * RATE)) / 0.004) * 0.05
    x[: len(hammer)] += hammer
    return x * vel * 0.22


def pad(m_list, dur, gain=0.1, attack=1.2):
    n = int((dur + 1.5) * RATE)
    t = t_axis(n)
    x = np.zeros((2, n))
    for m in m_list:
        f = hz(m)
        for c, det in enumerate((-0.07, 0.07)):
            for d2 in (-0.04, 0.0, 0.05):
                ph = 2 * math.pi * f * (1 + (det + d2) / 100) * t
                saw = sum(np.sin(ph * k) / k for k in range(1, 7))
                x[c] += saw
    for c in range(2):
        x[c] = fft_filter(x[c], hi=1400)
    e = np.minimum(1, t / attack) * np.clip((dur + 1.2 - t) / 1.2, 0, 1)
    x *= e
    return x / (np.abs(x).max() + 1e-9) * gain


PROG = {
    "warm": [["C3", "G3", "E4", "C5"], ["G2", "D3", "B3", "D5"], ["A2", "E3", "C4", "E5"], ["F2", "C3", "A3", "C5"]],
    "pensive": [["A2", "E3", "C4", "A4"], ["F2", "C3", "A3", "F4"], ["C3", "G3", "E4", "G4"], ["G2", "D3", "B3", "G4"]],
    "sparse": [["A2", "E4"], ["F2", "C4"], ["C3", "G4"], ["E2", "B3"]],
    "swell": [["F2", "C3", "A3", "C5"], ["G2", "D3", "B3", "D5"], ["A2", "E3", "C4", "E5"], ["C3", "G3", "E4", "G5"]],
    "hush": [["A2", "E3", "C4"], ["F2", "C3", "A3"]],
    "rise": [["F2", "C3", "A3", "F4"], ["G2", "D3", "B3", "G4"], ["A2", "E3", "C4", "A4"], ["B2", "D3", "G3", "B4"]],
    "drive": [["A2", "E3", "A3", "C5"], ["F2", "C3", "F3", "A4"], ["C3", "G3", "C4", "E5"], ["G2", "D3", "G3", "B4"]],
    "resolve": [["C3", "G3", "E4", "G4", "D5"], ["F2", "C3", "A3", "E5"]],
    "outro": [["F2", "C3", "A3", "C5"], ["C3", "G3", "E4", "G5"]],
}
STYLE = {  # (beats per chord, bpm, arpeggio pattern, piano vel, pad gain, level dB)
    "warm": (4, 76, "arp", 0.5, 0.05, -2),
    "pensive": (4, 68, "arp_slow", 0.42, 0.04, -4),
    "sparse": (4, 60, "single", 0.35, 0.03, -6),
    "swell": (4, 66, "block", 0.55, 0.12, 0),
    "hush": (8, 60, "none", 0.0, 0.07, -5),
    "rise": (2, 80, "arp", 0.55, 0.08, -1),
    "drive": (2, 96, "eighths", 0.6, 0.08, 0),
    "resolve": (6, 62, "block", 0.65, 0.14, 1),
    "outro": (6, 60, "arp_slow", 0.4, 0.07, -3),
}


def score(cues, total):
    n = int((total + 3) * RATE)
    out = np.zeros((2, n))
    for i, cue in enumerate(cues):
        t0 = cue["t"]
        t1 = cues[i + 1]["t"] if i + 1 < len(cues) else total + 1.5
        mood = cue["mood"]
        beats, bpm, pat, vel, pg, lvl = STYLE[mood]
        g = db(lvl)
        beat = 60 / bpm
        chord_len = beats * beat
        t = t0
        ci = 0
        prog = PROG[mood]
        while t < t1 - 0.05:
            chord = [midi(x) for x in prog[ci % len(prog)]]
            span = min(chord_len, t1 - t + 0.6)
            if pg:
                place(out, pad(chord[1:], span, pg * g), t)
            if pat == "arp":
                seq = chord[1:] + [chord[-1] + 12] + chord[1:][::-1]
                for k in range(int(span / (beat / 2))):
                    place(out, piano(seq[k % len(seq)], beat * 0.9, vel * (0.8 + 0.2 * (k % 4 == 0))) * g, t + k * beat / 2, pan=-0.3 + 0.6 * (k % 3) / 2)
                place(out, piano(chord[0], span, vel * 0.8) * g, t, pan=-0.2)
            elif pat == "arp_slow":
                seq = chord[1:]
                for k in range(int(span / beat)):
                    place(out, piano(seq[k % len(seq)], beat * 1.5, vel) * g, t + k * beat, pan=-0.2 + 0.4 * (k % 2))
                place(out, piano(chord[0], span, vel * 0.8) * g, t, pan=-0.2)
            elif pat == "single":
                place(out, piano(chord[-1], span, vel) * g, t + beat * 0.5)
                place(out, piano(chord[0], span, vel * 0.7) * g, t)
            elif pat == "block":
                for m in chord:
                    place(out, piano(m, span, vel * 0.7) * g, t + 0.012 * (m % 3), pan=(m % 7) / 7 - 0.5)
                place(out, piano(chord[-1] + 12, beat, vel * 0.5) * g, t + 2 * beat)
            elif pat == "eighths":
                for k in range(int(span / (beat / 2))):
                    place(out, piano(chord[(k % 3) + 1], beat * 0.45, vel * (0.9 if k % 2 == 0 else 0.7)) * g, t + k * beat / 2, pan=0.2 * ((k % 2) * 2 - 1))
                place(out, piano(chord[0], span, vel) * g, t)
            t += chord_len
            ci += 1
    return out[:, :n]


# ------------------------------------------------------------------ main


def main():
    ep, rd = Path(sys.argv[1]), Path(sys.argv[2])
    plan = json.loads((rd / "plan.json").read_text())
    fps = plan["fps"]
    total = plan["frames"] / fps
    n = int(total * RATE)
    sd = rd / "sound"
    sd.mkdir(exist_ok=True)
    cues = []

    # Dialogue: VO dry and close; on-screen lines get the room.
    dia = np.zeros((2, n))
    for L in plan["lines"]:
        x = decode(ep / L["file"]) * db(L.get("gain", 0.0))
        shot = next(s for s in plan["shots"] if s["id"] == L["shot"])
        if L["kind"] == "dialogue":
            buf = np.zeros((2, len(x) + RATE))
            place(buf, x, 0.0)
            size, wet = shot.get("reverb") or (0.6, 0.08)
            buf = convolve_stereo(buf, reverb_ir(size), wet)
            place(dia, buf, L["t0"])
        else:
            place(dia, x, L["t0"])
        cues.append(dict(kind="line", id=L["id"], t=L["t0"], frame=round(L["t0"] * fps) + 1))

    # Ambience: one continuous bed per scene with short crossfades at scene cuts.
    amb = np.zeros((2, n))
    scenes = []
    for s in plan["shots"]:
        if scenes and scenes[-1]["scene"] == s["scene"]:
            scenes[-1]["t1"] = s["t1"]
        else:
            scenes.append(dict(scene=s["scene"], amb=s.get("ambience"), t0=s["t0"], t1=s["t1"]))
    for i, sc in enumerate(scenes):
        if not sc["amb"]:
            continue
        a, b = sc["t0"], sc["t1"]
        m = int((b - a + 0.3) * RATE)
        bed = ambience(sc["amb"], m, seed=i)
        fade = int(0.08 * RATE)
        bed[:, :fade] *= np.linspace(0, 1, fade)
        bed[:, -fade:] *= np.linspace(1, 0, fade)
        place(amb, bed, a - 0.02)

    # Foley and sound effects from animation events.
    fx = np.zeros((2, n))
    for e in plan["events"]:
        k = e["kind"]
        pan = 0.0
        if k in ("step", "run_step"):
            sig = step(e["surface"], k == "run_step", e.get("scale", 1.0))
            g = 0.5 if k == "step" else 0.65
        elif k == "shuffle":
            sig, g = step(e["surface"]) * 0.5, 0.4
        elif k == "cloth":
            sig, g = cloth(0.7), 0.6
        elif k == "sit":
            sig, g = sit_sound(), 0.5
        elif k == "badge":
            hero = e["who"] == plan.get("narrator")   # the lead's counter gets the bright shimmer
            sig, g = chime("reveal" if hero else "tick"), 0.75 if hero else 0.4
        else:
            continue
        place(fx, sig, e["t"], g, pan)
        cues.append(dict(kind=k, who=e["who"], t=e["t"], frame=round(e["t"] * fps) + 1))
    for f in plan["fx"]:
        if f["kind"] == "ripple":
            place(fx, ripple(), f["t"], 0.7)
            cues.append(dict(kind="ripple", t=f["t"], frame=round(f["t"] * fps) + 1))
    fx = convolve_stereo(fx, reverb_ir(0.5), 0.12)

    # Score, ducked under speech.
    mus = score(plan["music"], total)[:, :n]
    if mus.shape[1] < n:
        mus = np.pad(mus, ((0, 0), (0, n - mus.shape[1])))
    mus = convolve_stereo(mus, reverb_ir(2.4, damp=5000, pre=0.02), 0.35)
    speech = np.abs(dia).max(axis=0)
    win = int(0.05 * RATE)
    envl = np.convolve(speech, np.ones(win) / win, mode="same")
    duck = np.clip(envl * 12, 0, 1)
    duck = np.convolve(duck, np.ones(int(0.3 * RATE)) / int(0.3 * RATE), mode="same")
    mus *= 1 - 0.45 * duck
    fade_out = int(1.5 * RATE)
    mus[:, -fade_out:] *= np.linspace(1, 0, fade_out)
    amb[:, -fade_out:] *= np.linspace(1, 0, fade_out)

    stems = {"dialogue": dia * db(0), "ambience": amb * db(-3), "fx": fx * db(-2), "music": mus * db(-9)}
    for k, v in stems.items():
        write(sd / f"{k}.wav", v)
    premix = sum(stems.values())
    write(sd / "premix.wav", premix)
    # Master: two-pass loudness normalisation to -14 LUFS, true peak -1.5 dB.
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(sd / "premix.wav"), "-af",
                        "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    meas = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    af = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:"
          "measured_thresh={input_thresh}:offset={target_offset}:linear=true").format(**meas)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sd / "premix.wav"), "-af", af, "-ar", str(RATE), "-c:a", "pcm_s24le", str(sd / "mix.wav")], check=True)
    (sd / "cues.json").write_text(json.dumps(dict(premix_loudness=meas, cues=sorted(cues, key=lambda c: c["t"])), indent=1))
    print(f"[sound] stems + mix in {sd} (premix {meas['input_i']} LUFS -> -14)")


if __name__ == "__main__":
    main()
