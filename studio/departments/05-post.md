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

Frames render to `renders/SLUG/<preset>/frames/` and existing frames are skipped, so an
interrupted render resumes. Delete the frames of the shots you changed before re-rendering.

## Delivery QC: `python3 scripts/studio.py deliver SLUG --preset final`

Encodes H.264 High / yuv420p / AAC 192 kbps / +faststart, then checks the actual file:
codec and pixel format, 1080x1920, 24 fps, audio present, decodes to the last frame,
−14 ± 1.5 LUFS, true peak ≤ −1 dBTP, duration matches the plan. It also runs the pacing
audit (`scripts/video_audit.py`, against `studio/quality-bar.json`: cuts per minute, frozen
stretches, motion) and writes a one-frame-per-shot contact sheet `frames.jpg`.

## Before posting (creator's decision)

Watch the final file on a phone, muted and unmuted. Check the first frame works as a
cover, captions clear the UI, the last line is readable, and the counters are readable in
every wide shot. Posting to Instagram is a separate action the creator takes or explicitly
authorises.
