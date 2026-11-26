# Generating sound for the shorts: voice, foley, ambience, score, mix (agent skill)

This is the method that produced the *One Hug Left* soundtrack, the best-sounding film the
studio has made so far. Treat it as the baseline: change one part at a time and compare
against it. Everything is generated offline on the CPU, with no paid service and no
language model, so a re-mix costs nothing.

The soundtrack has four stems, all built from the screenplay and the animation:

| Stem | Source | Gain into premix |
| --- | --- | --- |
| dialogue | TTS takes, placed at the line times; on-screen lines get room reverb, narration stays dry | 0 dB |
| ambience | one generated bed per scene (school yard, living room, hospital room, corridor) | −3 dB |
| fx | foley spotted from animation events (steps, cloth, sits, counter chimes, ripple) | −2 dB |
| music | procedural piano + string pad, mood cues per shot, ducked under speech | −9 dB |

Then a two-pass loudness normalisation to **−14 LUFS integrated, −1.5 dBTP**.

---

## 1. Tools and where they live

| Tool | Location | Purpose |
| --- | --- | --- |
| `.audio-tools/venv` (Python 3.12, uv) | numpy, kokoro-onnx, onnxruntime, phonemizer, espeakng-loader | voice processing, sound design, mix |
| `.audio-tools/asr` (Python 3.12) | faster-whisper, ctranslate2; model `small.en` in `~/.cache/huggingface/hub` | word timing, intelligibility checks |
| `.audio-tools/bin/crispasr` | CrispASR 0.8.55 (ggml-based speech toolkit) with the `qwen3-tts` backend | Qwen3-TTS voices |
| `.audio-tools/models/qwen3-tts-12hz-1.7b-customvoice-q8_0.gguf` + `qwen3-tts-tokenizer-12hz.gguf` | 2.0 GB + 0.36 GB | Qwen3-TTS CustomVoice 1.7B, 8-bit |
| `.audio-tools/models/kokoro-v1.0.int8.onnx` + `voices-v1.0.bin` | 92 MB + 28 MB | Kokoro 82M voices |
| system `ffmpeg` with `rubberband` | decode/encode, pitch and tempo, loudnorm, ebur128 | |

Rebuilding from nothing:

```sh
uv venv .audio-tools/venv --python 3.12 && .audio-tools/venv/bin/pip install numpy kokoro-onnx onnxruntime
uv venv .audio-tools/asr --python 3.12 && .audio-tools/asr/bin/pip install faster-whisper
.audio-tools/asr/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8')"   # downloads once
# crispasr: build CrispASR from source (ggml) with the qwen3-tts backend, put the binary and its .so files in .audio-tools/bin/,
# and download the two Qwen3-TTS GGUF files into .audio-tools/models/
```

After the first download, every model loads with `local_files_only=True`, so nothing
touches the network during production.

**PyAV pitfall:** faster-whisper's own audio loader (PyAV) failed on this machine. Always
decode with FFmpeg into a float32 numpy array and pass the array:

```python
def load16k(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       check=True, capture_output=True)
    return np.frombuffer(r.stdout, "<f4").copy()
```

All internal audio is **48 kHz float32**; stems are written as 24-bit PCM WAV.

---

## 2. Casting and directing the voices

### Engines

**Qwen3-TTS CustomVoice** is the main engine: it takes an English *instruction* that
directs the performance, which is what makes narration sound like a person remembering
instead of an announcer. Speakers used: `aiden` (younger male) and `ryan` (adult male).
Speed: about 4× real time on the CPU (18 lines in ~4 min).

```sh
OMP_NUM_THREADS=10 .audio-tools/bin/crispasr --backend qwen3-tts \
  -m .audio-tools/models/qwen3-tts-12hz-1.7b-customvoice-q8_0.gguf \
  --codec-model .audio-tools/models/qwen3-tts-tokenizer-12hz.gguf \
  --voice aiden --instruct "A young man remembering his childhood, warm and gentle, quiet, close to the microphone, natural storytelling, never an announcer." \
  --seed 211 -t 10 --no-gpu --tts "In my town, everyone is born with a number of hugs." --tts-output raw.wav
```

**Kokoro** (fast, many stock voices: `af_heart`, `af_sarah`, `am_michael`, `bm_george`, ...)
is the fallback and good for female or British voices:

```python
import onnxruntime as ort
from kokoro_onnx import Kokoro
opts = ort.SessionOptions(); opts.intra_op_num_threads = 8
sess = ort.InferenceSession("…/kokoro-v1.0.int8.onnx", opts, providers=["CPUExecutionProvider"])
k = Kokoro.from_session(sess, "…/voices-v1.0.bin")
pcm, rate = k.create(text, voice="af_heart", lang="en-us", speed=1.0)
```

### The voice table in the screenplay

```json
"voices": {
  "kai":       {"engine": "qwen3", "speaker": "aiden", "seed": 211,
                "instruct": "A young man remembering his childhood, warm and gentle, quiet, close to the microphone, natural storytelling, never an announcer."},
  "kai_child": {"engine": "qwen3", "speaker": "aiden", "seed": 307, "pitch": 2.5,
                "instruct": "A curious ten-year-old boy asking his grandfather a quiet question, soft and young."},
  "grandpa":   {"engine": "qwen3", "speaker": "ryan", "seed": 97, "pitch": -2.0, "tempo": 0.94,
                "instruct": "A tired old grandfather in a hospital bed, gentle, low and slightly raspy, speaking slowly with love."}
}
```

A line uses the voice named by `who`, or by `"voice": "kai_child"` to override.

### How to write a good instruction

Say who is speaking, to whom, how loud, the feeling, the pace, and "not an announcer":
*"A tired old grandfather in a hospital bed, gentle, low and slightly raspy, speaking
slowly with love."* A narrator is "close to the microphone, quiet". Vague instructions
("emotional") give a generic read.

### Ageing and de-ageing

There is no child voice, so age with pitch, and slow an elder with tempo, via FFmpeg's
rubberband filter with formants preserved (so the voice does not turn into a chipmunk):

```
rubberband=pitch={2 ** (semitones / 12):.4f}:tempo={tempo:.3f}:formant=preserved,highpass=f=70
```

+2.5 semitones turned the adult narrator into a believable ten-year-old;
−2 semitones and 0.94 tempo turned `ryan` into an old man.

### Fixing a mispronounced or misheard word

Keep the caption text in `text` and give the engine a spelling it speaks clearly in
`"say"`. Example: the child's line "Who's it for, Grandpa?" was misheard every time, so
`"say": "Who is it for, Grandpa?"` while captions still show "Who's it for". If one seed
gives a bad take, change `seed` (the take cache keys on text + settings, so it regenerates).

### Processing chain for every take

```
synthesise → rubberband pitch/tempo (optional) + 70 Hz high-pass → 48 kHz mono
→ trim silence → level to −20 dB active-speech RMS → peak ≤ 0.89 → write takes/<id>.wav
```

```python
RATE = 48000
def trim(pcm, thresh_db=-42, pad=0.05):
    win = int(RATE * 0.01); n = len(pcm) // win
    rms = np.sqrt(np.mean(pcm[:n*win].reshape(n, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    idx = np.where(db > thresh_db)[0]
    a = max(0, idx[0]*win - int(pad*RATE)); b = min(len(pcm), (idx[-1]+1)*win + int(pad*1.6*RATE))
    out = pcm[a:b].copy(); fade = int(0.012*RATE)
    out[:fade] *= np.linspace(0, 1, fade); out[-fade:] *= np.linspace(1, 0, fade)
    return out

def level(pcm, target_db=-20.0):
    win = int(RATE * 0.02); n = len(pcm) // win
    rms = np.sqrt(np.mean(pcm[:n*win].reshape(n, win) ** 2, axis=1) + 1e-12)
    active = rms[rms > rms.max() * 0.1]                       # ignore pauses
    g = 10 ** ((target_db - 20*np.log10(np.sqrt(np.mean(active**2)) + 1e-12)) / 20)
    out = pcm * g
    if np.abs(out).max() > 0.89: out *= 0.89 / np.abs(out).max()
    return out
```

Takes are cached: `manifest.json` stores a SHA-1 of `[spoken text, voice spec]` per line,
and a line is only re-synthesised when that key changes (or with `--force ID`).

### Mouth envelope for lip-sync

Per take, a 24 fps loudness curve that the animation uses to open the mouth:

```python
def envelope(pcm, fps=24):
    hop = RATE // fps; n = int(np.ceil(len(pcm) / hop))
    rms = np.sqrt(np.mean(np.pad(pcm, (0, n*hop - len(pcm))).reshape(n, hop) ** 2, axis=1))
    e = np.clip(rms / (np.percentile(rms, 95) + 1e-9), 0, 1)
    e = np.convolve(e, [0.25, 0.5, 0.25], mode="same")
    return np.concatenate([e[1:], e[-1:]])          # one frame early: mouths look better slightly ahead of sound
```

Acting maps it to mouth shapes: > 0.42 alternates `talk_a`/`talk_o` every 3 frames,
> 0.16 `talk_e`, > 0.04 a small closed mouth, else the expression's own mouth.

### Word timing and the voice gate

faster-whisper (`small.en`, int8, beam 5, `word_timestamps=True`) transcribes each take.
The **script's words are mapped onto the recognised words** with `difflib.SequenceMatcher`
(equal runs take their times directly; equal-length replacements map one to one; other
replacements spread the recognised span evenly; gaps are interpolated). Captions therefore
always show the written text, even when the recogniser mishears a name.

`match` = SequenceMatcher ratio between the *spoken* words (`say` if given) and what was
heard. **Gate: every take must reach match ≥ 0.8** before animation is timed to it.

Output `episodes/SLUG/voice/timing.json`:

```json
{"lines": {"n01": {"file": "voice/takes/n01.wav", "dur": 3.12, "env": [...], "env_fps": 24,
                   "words": [{"w": "In", "s": 0.05, "e": 0.18}, ...], "heard": "In my town ...", "match": 1.0}}}
```

Real line lengths now drive the edit: shot lengths are computed from them.

---

## 3. Placing dialogue

- A shot lists its lines; each line starts at the shot start + `lead` (0.25 s in the first
  shot, 0.12 s after), or at an explicit `"at"` seconds into the shot, and never before the
  previous line has ended + 0.10 s. The next line in the same shot follows after `gap`
  (default 0.14 s).
- Without an explicit `dur`, the shot lasts until its last line ends + `pad` (default
  0.3 s; use 0.45–1.0 s after a line that must land). `"spill": true` lets a line run
  across the cut into the next shot (J/L cut).
- **Narration (`kind: vo`) is dry and close.** **On-screen dialogue (`kind: dialogue`)
  gets the room's reverb** so it sounds like it happens in the place. Each set declares it
  as `s.reverb = (IR seconds, wet)` (and its footstep `s.surface`) in `sets.py`; the plan
  copies it onto every shot:

  | Set | IR length (s) | Wet |
  | --- | --- | --- |
  | school_gate | 0.6 | 0.06 |
  | grandpa_living | 0.5 | 0.10 |
  | hospital_room | 0.7 | 0.12 |
  | hospital_corridor | 1.6 | 0.18 |

- Per-line `gain` (dB) in the screenplay trims a single line.

---

## 4. Sound design recipes (numpy, 48 kHz)

Helpers: `t = np.arange(n) / RATE`; `noise(n, "white"|"pink"|"brown")`;
`fft_filter(x, lo, hi)` = 4th-order-ish high/low-pass in the frequency domain;
`place(buf, sig, t, gain, pan)` adds a mono (equal-power pan) or stereo signal into a
stereo buffer at time t. A fixed RNG seed (7) makes every mix reproducible.

### Foley is spotted from the animation, not by hand

The animation engine emits events while it solves the motion: a `step` at the exact
moment each foot lands, `run_step`, `shuffle` (turn in place), `sit`, `cloth` (0.3 s into
a hug), `badge` (a counter changes), and FX such as `ripple`. Each event carries the time,
the actor, the actor's scale and the floor surface of the set. Sound therefore lands on the
frame by construction. `sound/cues.json` lists every cue with its frame for review.

| Event | Sound | Gain |
| --- | --- | --- |
| step | `step(surface)` | 0.5 |
| run_step | `step(surface, run=True)` | 0.65 |
| shuffle | `step(surface) × 0.5` | 0.4 |
| cloth | `cloth(0.7)` | 0.6 |
| sit | `sit_sound()` | 0.5 |
| badge (the screenplay's `narrator`) | `chime("reveal")` | 0.75 |
| badge (everyone else) | `chime("tick")` | 0.4 |
| ripple fx | `ripple()` | 0.7 |

The whole fx stem gets a short room (0.5 s IR, 12 % wet).

```python
def step(surface, run=False, scale=1.0):           # 160 ms
    n = int(0.16 * RATE); t = np.arange(n) / RATE
    f, dec = {"concrete": (95, 0.02), "wood": (120, 0.05), "vinyl": (140, 0.025)}.get(surface, (110, 0.03))
    thump = np.sin(2*np.pi * f / max(scale, 0.6) * t) * np.exp(-t / dec)       # smaller people = higher thump
    grit = fft_filter(noise(n), lo=900 if surface != "vinyl" else 1500,
                      hi=6000 if surface == "concrete" else 4000) * np.exp(-t / 0.018)
    x = thump * 0.7 + grit * (0.55 if surface == "concrete" else 0.3)
    if surface == "vinyl":                                                      # rubber-sole squeak
        x += 0.12 * np.sin(2*np.pi*2400*t) * np.exp(-t/0.01) * rng.uniform(0.3, 1)
    return x * (1.5 if run else 1.0) * rng.uniform(0.75, 1.0) * (0.6 + 0.4*scale)

def cloth(dur=0.6):                                 # hug rustle
    n = int(dur*RATE); t = np.arange(n)/RATE
    return fft_filter(noise(n, "pink"), lo=300, hi=3500) * np.sin(np.pi*np.clip(t/dur, 0, 1))**2 * 0.5

def chime(kind="tick"):                             # the counter's voice
    n = int(1.6*RATE); t = np.arange(n)/RATE
    if kind == "reveal":                            # bright C-major shimmer + upward sweep
        x = sum(np.sin(2*np.pi*f*t + i) * np.exp(-t/(0.9 - 0.12*i)) * 0.5**i
                for i, f in enumerate([1046.5, 1318.5, 1568.0, 2093.0]))
        sweep = np.sin(2*np.pi*(600*t + 900*t*t)) * np.exp(-t/0.25) * 0.15
        return (x*0.35 + sweep) * np.minimum(1, t/0.01)
    x = sum(np.sin(2*np.pi*f*t) * np.exp(-t/0.35) * 0.6**i for i, f in enumerate([1567.98, 2349.3]))
    return x * 0.3 * np.minimum(1, t/0.004)

def ripple():                                       # the invisible wall: glassy falling tone + soft thud
    n = int(1.2*RATE); t = np.arange(n)/RATE
    f = 220 + 520*np.exp(-t/0.18); ph = np.cumsum(2*np.pi*f/RATE)
    x = np.sin(ph) * np.exp(-t/0.35) * 0.45
    x += sum(np.sin(2*np.pi*fr*t) * np.exp(-t/0.6) * 0.08 for fr in (1760, 2217, 2637))
    return x + np.sin(2*np.pi*70*t) * np.exp(-t/0.08) * 0.5

def sit_sound():
    n = int(0.4*RATE); t = np.arange(n)/RATE
    return np.sin(2*np.pi*85*t)*np.exp(-t/0.06)*0.5 + fft_filter(noise(n, "pink"), lo=200, hi=2500)*np.exp(-t/0.12)*0.3
```

**Signature sounds matter.** The story's rule (the counters) has its own sound: a small
two-note tick for everyone else and a brighter shimmer when the hero's number changes.
Give every new world rule a sound the viewer learns in the hook.

### Ambience: one continuous bed per scene

Never cut ambience per shot (it makes every cut audible). Build one bed per scene
(+0.3 s), 80 ms fades, placed 20 ms before the scene starts so beds overlap across scene cuts.

- **schoolyard**: pink noise 120–1800 Hz × 0.05 per channel; band-limited "chatter" noise
  (300–2600 Hz) gated by a slow random envelope; ~0.7 bird chirps per second (2.8–4.2 kHz
  sine sweeps of 0.25 s, random pan).
- **living_room**: brown-noise room tone under 400 Hz × 0.05; a clock tick every second
  (2 kHz+ noise click, alternating 0.05/0.035, panned −0.4); faint 1.5–5 kHz air.
- **hospital_room**: mains hum (60/120/180 Hz at 0.02/0.012/0.006); pink noise 80–900 Hz;
  heart-monitor beep (987.8 Hz, 110 ms) every 0.92 s, panned +0.5.
- **corridor**: fluorescent buzz (square + sine at 120 Hz, very quiet); HVAC pink noise
  60–1200 Hz; distant intermittent noise.

New locations follow the same idea: 1 broadband bed + 1 identifying periodic detail (a
clock, a monitor, birds) + occasional random events.

### Reverb

A cheap convolution reverb that sounds natural enough: stereo Gaussian noise with an
exponential decay to −60 dB over the IR length, low-passed (6 kHz; 5 kHz for music), 12 ms
pre-delay, energy-normalised. Mix wet/dry by FFT convolution.

```python
def reverb_ir(seconds=1.8, damp=6000, pre=0.012):
    n = int(seconds*RATE); t = np.arange(n)/RATE
    ir = rng.standard_normal((2, n)) * np.exp(-t * 6.9 / seconds)
    for c in range(2): ir[c] = fft_filter(ir[c], hi=damp)
    ir = np.concatenate([np.zeros((2, int(pre*RATE))), ir], axis=1)
    return ir / np.sqrt(np.sum(ir**2) / 2)
```

---

## 5. The score (procedural piano + pad)

The screenplay places **mood cues** on shots (`{"at": "sh15", "mood": "swell", "offset": 0.6}`);
each mood plays until the next cue. Keys: C major / A minor, so every mood is compatible
and transitions never clash.

```python
PROG = {   # chords, bass note first
  "warm":    [["C3","G3","E4","C5"], ["G2","D3","B3","D5"], ["A2","E3","C4","E5"], ["F2","C3","A3","C5"]],
  "pensive": [["A2","E3","C4","A4"], ["F2","C3","A3","F4"], ["C3","G3","E4","G4"], ["G2","D3","B3","G4"]],
  "sparse":  [["A2","E4"], ["F2","C4"], ["C3","G4"], ["E2","B3"]],
  "swell":   [["F2","C3","A3","C5"], ["G2","D3","B3","D5"], ["A2","E3","C4","E5"], ["C3","G3","E4","G5"]],
  "hush":    [["A2","E3","C4"], ["F2","C3","A3"]],
  "rise":    [["F2","C3","A3","F4"], ["G2","D3","B3","G4"], ["A2","E3","C4","A4"], ["B2","D3","G3","B4"]],
  "drive":   [["A2","E3","A3","C5"], ["F2","C3","F3","A4"], ["C3","G3","C4","E5"], ["G2","D3","G3","B4"]],
  "resolve": [["C3","G3","E4","G4","D5"], ["F2","C3","A3","E5"]],
  "outro":   [["F2","C3","A3","C5"], ["C3","G3","E4","G5"]],
}
STYLE = {  # beats per chord, bpm, pattern, piano velocity, pad gain, level dB
  "warm": (4, 76, "arp", 0.5, 0.05, -2),      "pensive": (4, 68, "arp_slow", 0.42, 0.04, -4),
  "sparse": (4, 60, "single", 0.35, 0.03, -6), "swell": (4, 66, "block", 0.55, 0.12, 0),
  "hush": (8, 60, "none", 0.0, 0.07, -5),      "rise": (2, 80, "arp", 0.55, 0.08, -1),
  "drive": (2, 96, "eighths", 0.6, 0.08, 0),   "resolve": (6, 62, "block", 0.65, 0.14, 1),
  "outro": (6, 60, "arp_slow", 0.4, 0.07, -3),
}
```

Patterns: `arp` = chord tones up an octave and back in eighth notes plus the bass held;
`arp_slow` = one chord tone per beat; `single` = top note on the off-beat plus bass;
`block` = all notes with a 12 ms strum and an octave accent on beat 3; `eighths` =
repeated eighths on the inner voices; `none` = pad only.

**Piano voice** (additive): up to 8 partials with slight inharmonicity
(`f_k = f·k·sqrt(1 + 0.0004·k²)`), each decaying faster for higher partials and higher
notes (`decay = (2.8 − 0.18k)·(220/f)^0.35`), amplitude 0.55^(k−1), brighter with velocity,
3 ms attack, 0.25 s release, plus a 20 ms filtered noise "hammer". **Pad**: detuned saw
stacks (±0.07 % per channel, three voices each), low-passed at 1.4 kHz, 1.2 s attack and
release, normalised to the pad gain.

The music stem gets a long hall (2.4 s IR, 5 kHz damping, 20 ms pre-delay, 35 % wet).

**Writing the cue sheet.** Warm under the world rule; pensive for the mystery; *sparse or
hush right before the reveal*; swell on the turn; hush on the reveal line itself (let the
words land in near-silence); rise → drive for the choice (the hero runs); resolve on the
payoff; outro under the button line with a 1.5 s fade at the end. The pattern
"music → quiet → line → effect → music" is the most reliable emotional device we have.

**Ducking.** Speech envelope = |dialogue| smoothed over 50 ms, ×12, clipped to 1, then
smoothed over 300 ms. Music × (1 − 0.45·duck). Music and ambience fade out over the last 1.5 s.

---

## 6. Mix and master

```python
stems = {"dialogue": dia, "ambience": amb * db(-3), "fx": fx * db(-2), "music": mus * db(-9)}
premix = sum(stems.values())   # write each stem and the premix as 24-bit WAV
```

Two-pass loudnorm (measure, then apply with the measured values and `linear=true`, so it
is a clean gain change rather than dynamic compression):

```sh
ffmpeg -i premix.wav -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null -
# read input_i, input_tp, input_lra, input_thresh, target_offset from the JSON on stderr, then:
ffmpeg -y -i premix.wav -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=…:linear=true" \
       -ar 48000 -c:a pcm_s24le mix.wav
```

−14 LUFS is the usual loudness for Instagram Reels and TikTok; quieter files sound weak next
to other reels, louder ones are turned down and lose punch. After encoding to AAC the
delivered file measured −14.1 LUFS and −1.3 dBTP. Delivery QC accepts −14 ± 1.5 LUFS and
true peak ≤ −1 dBTP, measured on the **final mp4** with `ebur128=peak=true`.

Outputs in `renders/SLUG/<preset>/sound/`: `dialogue.wav`, `ambience.wav`, `fx.wav`,
`music.wav`, `premix.wav`, `mix.wav`, `cues.json`.

---

## 7. The listening gate (what an agent can and cannot check)

Run faster-whisper over the **final mix**, line by line (from 0.1 s before each line to
0.25 s after), and compare with the script after spelling out numbers ("10" → "ten") and
expanding contractions ("it's" → "it is"). **Gate: worst line ≥ 0.6.** *One Hug Left*
scored 1.00 on every line through the mix. If a line fails: raise its `gain`, lower
the music under it (a `hush` cue), or remove a competing effect.

What the numbers cannot tell you: whether the score is too loud emotionally, whether the
old man sounds moving or silly, whether the chimes are annoying. **An agent must say plainly
that nobody has listened by ear** until a human listens on headphones and on a phone speaker
and writes the note in `episodes/SLUG/notes.md` (who listened, on what device, what they heard).

---

## 8. Captions (they are part of the sound design for mute viewers)

About half of Reels viewers watch muted. Captions are word-timed ASS subtitles burned into
the main file; a clean file without them is delivered too.

- 1080×1920 canvas, Fira Sans Bold, 86 px narration / 82 px dialogue, 7 px dark outline,
  3 px shadow, bottom-centred with `MarginV 600` (≈31 % up from the bottom: below the
  faces, above the Reels caption/button area).
- Chunks of at most 3 words / 16 characters, also breaking after punctuation.
- Each chunk pops in (scale 82 % → 100 % in 90 ms); the word being spoken turns gold
  (`&H0047D6FF&`, ASS colours are BGR).
- Narration upper case and white; dialogue italic and tinted per speaker: the narrator
  gets warm yellow, then each other speaker the next tint in order of their first line
  (blue, pink, green, lilac). A cast entry may override with `"caption_color": "#RRGGBB"`.
- **Never show two events at once**: sort by start and cut each event where the next one
  begins. Overlapping events once rendered as "MOSHUNDREDS".
- Times come from the aligned word timings, so captions follow the voice exactly.

Burn with `-vf "subtitles=captions.ass:fontsdir=/usr/share/fonts/TTF"` (the folder of the font
`kit/fonts.py` found; `captions.build(..., font=family)` names that family in the styles).

---

## 9. Checklist for a new episode's sound

1. Every speaking role has a voice with a specific instruction; children are aged with pitch.
2. `studio.py voice SLUG`: all takes ≥ 0.8 match; fix with `say` or a new `seed`.
3. Every scene has an `ambience`; a new location gets a new bed recipe in `sound.py`, and
   its set declares `surface` and `reverb`.
4. The world rule has a signature sound, heard in the hook.
5. Music cues: quiet before the reveal, a lift on the choice, a resolve on the payoff, an outro.
6. `studio.py sound SLUG --preset preview`: worst line through the mix ≥ 0.6.
7. Delivery: −14 ± 1.5 LUFS, true peak ≤ −1 dBTP on the mp4.
8. Write in `notes.md` whether a human has listened. If not, say so in the report.

## 10. Ideas that are not done yet (try them against the baseline)

- A real piano sample set or a small SoundFont instead of the additive piano.
- Breaths and small mouth sounds before emotional dialogue lines.
- Per-shot ambience perspective (closer/farther) inside one scene bed.
- A low sub "heartbeat" under the reveal; a reversed swell into the turn.
