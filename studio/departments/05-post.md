# Post — editor, compositor/colourist, captions, delivery QC

## Edit

The cut is defined by the screenplay: shot durations come from the measured takes plus
`pad`, or an explicit `dur`. To retime, change `pad`/`dur`/`"at"` in the screenplay and
rebuild; never trim rendered frames by hand. Typical rhythm: 1.5–3 s per shot, the hook
under 4.5 s, a 1.5–2 s hold after the reveal and on the final image.

## Captions (`scripts/kit/captions.py`)

Burned in for mute viewing: 1–3 word chunks that pop in, the spoken word in gold, Fira
Sans Bold with a heavy outline, at about 31% of the frame height (below faces, above the
Reels caption and button area). Narration is upper case and white; dialogue is italic and
tinted per speaker. Word times come from the aligned takes, so captions follow the voice.
A clean version without captions is delivered too.

## Render presets

| Preset | Size | Engine | Speed on GTX 1650 | Use |
| --- | --- | --- | --- | --- |
| `board` | 360x640 | Workbench | ~0.1 s/frame | storyboard, animatic |
| `preview` | 540x960 | Eevee, 24 samples | ~0.4–0.5 s/frame | director review |
| `final` | 1080x1920 | Eevee, 32 samples | ~3 s/frame (~75 min per minute of film) | delivery |
| `final_hq` | 1080x1920 | Eevee, 64 samples, ray tracing | ~9 s/frame | hero frames, covers |

Frames render to `renders/SLUG/<preset>/frames/` with `manifest.json`, which records the
plan each frame was rendered from. A full render keeps the frames already rendered from the
plan it is about to build (an interrupted render resumes) and deletes stale, empty (Blender's
placeholder for an interrupted frame) and unrecorded frames. `--frames A-B` deletes that range
first so it is really re-rendered. A change to the plan makes every frame stale: `render` and
`deliver` refuse missing, extra, empty, edited or older-plan frames.

## Delivery QC: `python3 scripts/studio.py deliver SLUG --preset final`

Encodes H.264 High / yuv420p / AAC 192 kbps / +faststart, then checks the actual file:
codec and pixel format, 1080x1920, 24 fps, audio present, decodes to the last frame,
−14 ± 1.5 LUFS, true peak ≤ −1 dBTP, duration matches the plan. Before encoding it refuses
frames that are not all current and a mix whose sound gate failed or whose inputs changed.
It runs the pacing audit (`scripts/video_audit.py` against `studio/quality-bar.json`) and
writes a one-frame-per-shot contact sheet `frames.jpg`.

| Pacing result | Codes | Effect |
| --- | --- | --- |
| **Hard** (defects) | `frozen_picture` (> 1.5 s outside a shot's `hold`), `flash_frames`, `opens_on_black`, `audio_peak` | delivery fails |
| **Artistic** (taste) | `cuts_per_minute`, `median_shot`, `longest_shot`, `static_shots`, `mean_motion`, `first_second_motion`, `duration`, `loudness` | delivery passes; each needs a disposition in the picture review |
| **Excused** | frozen picture inside a shot with `"hold": "why"` | listed, accepted |

## Picture review (checkpoint)

Watch the captioned movie at phone size (muted, then eyes closed), look at `frames.jpg` and
single frames at the hook, reveal and button, then record it:

```sh
python3 scripts/studio.py review SLUG picture --preset preview --verdict approve|revise \
    --saw "what you saw, with shot ids and seconds" --reviewer "who, and how (phone? listened?)" \
    --note first_second_motion="why it is right, or what will change"
```

Approval needs a `--note` for every artistic code `deliver` printed. The review is tied to the
exact movie and contact sheet; a new delivery voids it. `status` prints `DONE` when the final
is delivered, current and approved.

## Before posting (creator's decision)

Watch the final file on a phone, muted and unmuted. Check the first frame works as a
cover, captions clear the UI, the last line is readable, and the counters are readable in
every wide shot. Posting to Instagram is a separate action the creator takes or explicitly
authorises.
