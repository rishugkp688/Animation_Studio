# Sound department — casting, voice, sound design, score, mix

## Voice

Cast every speaking role in `voices` and point each line at a voice (`who`, or `voice` to
override, e.g. a younger take of the narrator).

```json
"kai":       {"engine": "qwen3", "speaker": "aiden", "instruct": "A young man remembering his childhood, warm and gentle, quiet, close to the microphone, natural storytelling, never an announcer.", "seed": 211},
"kai_child": {"engine": "qwen3", "speaker": "aiden", "instruct": "A curious ten-year-old boy...", "pitch": 2.5},
"grandpa":   {"engine": "qwen3", "speaker": "ryan",  "instruct": "A tired old grandfather...", "pitch": -2.0, "tempo": 0.94}
```

- **Engines**: `qwen3` (Qwen3-TTS CustomVoice via `.audio-tools/bin/crispasr`; speakers
  `ryan`, `aiden`; `instruct` directs the performance; Apache-2.0) and `kokoro`
  (`af_heart`, `af_sarah`, `am_michael`, `bm_george`, ...). Both run offline on the CPU.
- **Direction** goes in `instruct`: who is speaking, to whom, how loud, the feeling, and
  "not an announcer". `pitch` (semitones) and `tempo` age or de-age a voice.
- If a word comes out wrong, keep the caption text and add `"say": "..."` with a spelling
  the model pronounces correctly.
- `studio.py voice SLUG` trims, levels, aligns words with faster-whisper and writes
  `voice/timing.json`. Takes are cached by text and settings; `--force ID` redoes one.
- **Gate**: the recogniser must understand each take (match ≥ 0.8) before animation is
  timed to it.

## Sound design (generated in `scripts/kit/sound.py`)

- **Ambience** per scene (`ambience` on the scene): `schoolyard`, `living_room`
  (room tone, clock), `hospital_room` (hum, heart monitor), `corridor` (fluorescent
  buzz, HVAC). One continuous bed per scene, crossfaded at scene cuts.
- **Foley is spotted from the animation**: every footstep (surface from the set), run
  step, hug cloth, sit, turn shuffle and counter tick is an event the animation emitted,
  so sound and picture agree by construction. `renders/.../sound/cues.json` lists each cue
  with its frame for review.
- **Signature sounds**: counter tick (two-partial chime), the hero's counter (a brighter
  shimmer), the invisible wall (glassy sweep plus a soft thud).
- **Dialogue** in a scene gets that room's reverb; narration stays dry and close.

## Score

`music` in the screenplay places mood cues on shots: `warm`, `pensive`, `sparse`, `swell`,
`hush`, `rise`, `drive`, `resolve`, `outro`. The composer module plays piano and string-pad
parts in C major / A minor with reverb. It ducks 45% under speech and fades out at the end.
Put `hush` or `sparse` before a reveal and `swell`/`resolve` on the payoff. Silence is a cue too.

## Mix and master

Stems (dialogue, ambience, fx, music) are written as 24-bit WAV, summed to a premix, then
two-pass loudness-normalised to **−14 LUFS integrated, −1.5 dBTP** (Instagram/TikTok
practice). **Gate** (`studio.py sound`): the recogniser hears every line through the full
mix (worst match ≥ 0.6, numbers spelled out). An agent cannot judge taste by ear: a
listening note in `notes.md` says who listened, on what device, and what they heard.
