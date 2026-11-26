# Tools and components guide (plain-language)

A general guide for any agent asked to make short animated films with code: which tools
are used, what each one is for, why it was chosen, how to think about it, and the mistakes
that waste the most time. It is written to be useful on any project of this kind, not just
this repository. The other files in `Agent_Docs/` go deeper and show this studio's exact code.

---

## 1. The big picture: how a film gets made with code

An animated short is a chain of steps. Each step takes the previous result and adds one
layer:

```
idea → script → voices → motion and staging → pictures (render) → sound → edit + captions → video file
```

Five principles keep this chain reliable:

1. **One source of truth.** Write the whole film as data (a script file listing characters,
   lines, scenes, shots, actions, camera choices). Every tool reads that file. If something
   is wrong, change the file and run the steps again, never patch an output by hand.
2. **Cheap checks before expensive work.** Rendering pictures takes hours; checking a
   script or a pose takes seconds. Check the story, the voices and the motion first, then render.
3. **Every step has a pass/fail gate.** A step is not finished because it ran; it is finished
   when its result meets a measurable bar (no collisions, every line understandable, the right
   loudness...). Stop at the first failure.
4. **Check the real output.** A program exiting without an error proves nothing. Open the
   image, measure the audio, probe the video file.
5. **Be honest about what you cannot check.** An agent can measure loudness and run speech
   recognition, but it cannot *hear* whether a voice is moving or *feel* whether a story works.
   Say so, and leave that judgement to a person.

---

## 2. Tool map

| Job | Tool | One-line reason |
| --- | --- | --- |
| 3D scenes, characters, cameras, lights, rendering | **Blender** | free, complete, fully scriptable with Python, runs without a screen |
| Encoding video, mixing, filtering, measuring audio and video | **FFmpeg** (+ ffprobe) | the universal media toolbox; one command line does almost anything |
| Burned-in captions | **ASS subtitles** rendered by **libass** inside FFmpeg | word-by-word timing, colours, outlines, pop animations |
| Changing a voice's pitch or speed | **Rubber Band** (an FFmpeg filter) | high-quality pitch/tempo change that keeps the voice natural |
| Text-to-speech with acting direction | **Qwen3-TTS** run through a ggml-based program (CrispASR) | takes an instruction like "tired old man, speaking slowly with love" |
| Fast text-to-speech with many stock voices | **Kokoro** run with **ONNX Runtime** | small, fast on CPU, good female and British voices |
| Speech recognition (word timing, "can you understand this?") | **faster-whisper** (Whisper on CTranslate2) | accurate, runs offline on CPU, gives a time for every word |
| Number crunching for audio | **Python + NumPy** | generate and mix sound as arrays of numbers |
| Drawing 2D images (faces, contact sheets) | **Pillow (PIL)** | simple drawing of shapes and text into PNGs |
| Isolated Python environments | **venv / uv** | each tool gets the library versions it needs without conflicts |
| Fonts | system fonts (e.g. Fira Sans) | bold, readable captions and on-screen text |

Everything runs **offline on the local machine**. There are no paid services, no uploads,
and no language model in the pipeline, so re-running any step is free.

---

## 3. Blender

### What it is

Blender is a free 3D program: modelling, materials, lighting, animation, cameras and
rendering in one application. Everything you can click in its interface can also be done
from **Python** through its built-in `bpy` module. That is what makes it usable by an agent.

### How to run it as an agent

- **Without a window** ("background mode"): `blender -b -P my_script.py -- my arguments`.
- **With factory settings**: add `--factory-startup` so the user's personal add-ons and
  preferences cannot change the result.
- **Make failures visible**: add `--python-exit-code 1`. Without it, Blender exits as if
  everything went fine **even when your script crashed**. This single flag prevents the most
  confusing class of bugs.
- Blender ships its **own Python**. If another Python (pyenv, a venv) is active in the
  environment, Blender may pick up the wrong libraries. Launch it with a clean environment.

### The concepts you need

| Concept | Meaning in plain words |
| --- | --- |
| **Object** | anything placed in the scene: a mesh, a camera, a light, an empty (an invisible handle) |
| **Mesh** | the shape: points, edges and faces. Build it from code (a box, a turned profile) rather than clicking |
| **Parenting** | an object follows its parent. Characters can be a tree of invisible handles (hips, neck, shoulders) with body blocks attached |
| **Material** | how a surface looks: colour, roughness, shine, glow. Built from connected "nodes" |
| **Collection** | a folder of objects (one per location, one for the cast, one for cameras) |
| **Keyframe** | a value stored at a frame (position at frame 10, rotation at frame 20). Blender fills in the frames between |
| **Interpolation** | how values move between keyframes: *linear* (steady), *Bezier* (eased), *constant* (jumps). Use constant for things that switch (visibility, expression cells) |
| **Custom property** | your own number on an object (e.g. "which mouth shape"); materials can read it, and it can be keyframed |
| **Camera + markers** | one camera per shot; timeline markers tell Blender which camera is active from which frame. That is how cuts happen |
| **World** | the background sky colour/light |
| **Render engine** | the program that turns the scene into pictures (below) |
| **Colour management** | how light values become screen colours. "AgX" with a contrast look gives natural, filmic colour |

### Render engines and when to use them

| Engine | What it is | Use it for |
| --- | --- | --- |
| **Workbench** | instant viewport-style shading, no real lighting | storyboards and quick layout checks (fractions of a second per frame) |
| **Eevee** | real-time engine using the graphics card; good lighting and shadows | previews and finals on modest GPUs (seconds per frame) |
| **Cycles** | physically based path tracer; most realistic | hero stills or when the machine is strong; much slower |

Workbench ignores node materials and shows each material's simple "viewport colour", so set
that colour too, or every storyboard is grey.

### Good habits

- **Compute motion outside Blender, then hand Blender the results.** If walking, gestures
  and camera moves are computed in plain Python, the same numbers can be checked for errors
  before rendering. Blender only stores them as keyframes and draws them.
- **Key every frame** of computed motion with linear interpolation; easing has already been
  done in your own code.
- **Unique object names.** Blender silently renames duplicates ("wall.001"). Code that later
  looks objects up by name then grabs the wrong one. Prefix names with their location or character.
- **Lights should not leak** between locations placed in the same scene: limit their range.
- **Render to numbered PNG frames**, not straight to video. An interrupted render can resume,
  and single frames can be re-rendered after a fix.
- **Test with a few still frames** before rendering everything; use them to estimate time.
- **Save the scene file** for inspection, but never "fix" the film by editing it; rebuild from the source.
- Blender's Python API changes between versions (renamed engines, options, animation data
  layout). Guard optional settings and check the version you have.

### Style notes for blocky, Roblox-like characters

- Bodies are bevelled boxes on rigid joints (no skin deformation): legs, torso, arms, and a
  rounded-cylinder head, in fixed proportions.
- Faces are flat printed decals (eyes, brows, mouth, tears) swapped per frame; pupils can
  slide to show where the character looks. The face carries most of the acting.
- Strong silhouettes and one signature colour per main character, so each reads at phone size.
- Soft procedural texture (slight noise, wood grain, bricks) keeps surfaces from looking flat.

---

## 4. FFmpeg and ffprobe

### What they are

**FFmpeg** reads, converts, filters and writes almost any audio or video format.
**ffprobe** reports what a media file actually contains (codec, size, frame rate, duration).

### What we use FFmpeg for

| Task | What happens |
| --- | --- |
| Frames → video | numbered PNG frames + the final mix become an H.264 MP4 |
| Burn captions | the `subtitles` filter draws an ASS subtitle file onto the picture |
| Decode audio for Python | any audio file → raw numbers that NumPy can read |
| Voice processing | pitch/tempo change (Rubber Band), high-pass filter, resampling |
| Loudness | `loudnorm` sets the final loudness; `ebur128` measures it |
| Checks | decode the whole file to catch corruption; extract single frames to look at; build a contact sheet (one frame per shot in a grid) |

### Delivery settings that work for Instagram, TikTok and Shorts

- Video **H.264**, pixel format **yuv420p** (others may not play on phones), profile High,
  "faststart" (the file starts playing before it fully downloads).
- **1080×1920** (9:16 vertical), **24 or 30 fps**, constant frame rate.
- Audio **AAC**, 48 kHz, ~192 kbps.
- Loudness about **−14 LUFS** integrated, true peak below −1 dB.

### Habits

- After encoding, **probe the file**, **decode it completely** (errors appear if anything is
  broken), **measure loudness** on the final file, and **look at frames** from it.
- FFmpeg options change over the years (for example `-vsync` became `-fps_mode`). If a
  command fails, check the option against the installed version.

---

## 5. Captions: ASS subtitles and libass

ASS ("Advanced SubStation Alpha") is a subtitle format with styles and animation tags:
font, size, colour, outline, shadow, position, and per-word effects. FFmpeg uses the libass
library to draw it onto the video.

Short-form captions that work:

- 1–3 words at a time, bold font, heavy dark outline, placed in the lower middle of the
  frame (below faces, above the app's own buttons and caption area).
- The word being spoken highlighted in a different colour; each chunk "pops" in.
- Narration in one style (e.g. white capitals), dialogue in another (e.g. italic, tinted per speaker).
- Timing from speech recognition word times, so captions follow the actual voice.
- Never let two caption events overlap in time, or they draw on top of each other.
- Deliver one version with burned captions and one clean version.

---

## 6. Voices: text-to-speech

### Two engines and why

- **Qwen3-TTS (CustomVoice)**: a speech model that accepts a natural-language *instruction*
  for how to perform a line ("a young man remembering his childhood, quiet, close to the
  microphone, never an announcer"). This is what makes narration sound human. It runs on the
  CPU through a program built on **ggml** (a small C library for running AI models on ordinary
  hardware), using a compressed 8-bit model file. About 4× faster than real time here.
- **Kokoro**: a small, fast speech model with many ready-made voices (American and British,
  male and female). It runs with **ONNX Runtime**, a general engine for running exported AI
  models on the CPU. No acting instructions, but reliable and quick.

### How to get good performances

- **Direct, don't describe.** Say who is speaking, to whom, how loud, the feeling and the
  pace. Vague words like "emotional" give generic reads.
- **Age a voice with pitch and tempo.** Raising an adult voice ~2–3 semitones makes a
  believable child; lowering ~2 semitones and slowing ~6 % makes an elder. Use a formant-preserving
  pitch shifter (Rubber Band) so it doesn't sound like a cartoon.
- **Fix mispronunciations with a respelling** sent to the engine while captions keep the
  real text ("Who is it for?" spoken, "Who's it for?" shown). Or try another random seed.
- **Clean every take**: trim silence, remove rumble below ~70 Hz, level all lines to the
  same loudness, cap peaks.
- **Cache takes** by their text and settings so unchanged lines are never regenerated.

### Check every take with speech recognition

Run speech recognition on each take and compare with the script. If the recogniser can't
understand it, a viewer may not either. Re-record before animating to it. Real take lengths
then set the timing of the edit, and their loudness curve can drive mouth movement (lip-sync).

---

## 7. Speech recognition: faster-whisper

**Whisper** is a speech-recognition model; **faster-whisper** runs it efficiently on the CPU
using **CTranslate2**. It is used three ways:

1. **Word timing**: when each word in a take starts and ends (for captions and lip-sync).
2. **Take check**: does what it heard match the script?
3. **Mix check**: can every line still be understood once music and effects are added?

Habits: download the model once, then run offline. Decode audio with FFmpeg yourself and pass
raw numbers if the library's own loader misbehaves. Spell out numbers and expand contractions
before comparing ("10" vs "ten", "it's" vs "it is"). Map the *script's* words onto the
recognised timings, so a misheard name never appears in captions.

---

## 8. Sound design with NumPy

Sound is a list of numbers (48,000 per second). With NumPy you can generate, filter, place
and mix sounds directly, which means sound can be **computed from the animation itself**.

The soundtrack has four layers ("stems"):

| Stem | What it is | Tips |
| --- | --- | --- |
| **Dialogue** | the voice takes placed at their times | narration dry and close; on-screen speech gets a little of the room's echo |
| **Ambience** | the sound of the place (school yard chatter and birds, room tone and a clock, hospital hum and a heart monitor) | one continuous bed per scene, crossfaded at scene changes, never cut per shot |
| **Foley / effects** | footsteps, clothes rustle, sitting, story sounds (a chime when a counter changes) | trigger them from animation events (the frame a foot lands), so they are always in sync |
| **Music** | simple synthesised piano and string pad following mood cues | keep everything in one key family so moods connect; drop to near-silence before the line that matters |

Useful techniques in plain terms:

- **Synthesis**: sine waves with decays make chimes and piano notes; filtered noise makes
  steps, cloth, air and crowd; a falling tone makes a "magic barrier" sound.
- **Reverb** (room echo): convolve with a short burst of decaying noise; longer for corridors.
- **Ducking**: lower the music automatically while someone speaks.
- **Panning**: place sounds left/right for space.
- **Signature sounds**: give the story's visible rule its own sound, heard in the first seconds.

### Mixing and loudness

Set relative levels (dialogue loudest, then effects, ambience, music lowest), add the stems,
then normalise the result to the platform target. **LUFS** (loudness units) measures how
loud something *sounds* over time; **true peak** measures the highest point after conversion.
Short-video platforms play at about **−14 LUFS**; quieter files sound weak, louder ones get
turned down. A two-pass normalisation (measure, then apply exactly) gives a clean result.

---

## 9. Pillow (PIL) and Python environments

- **Pillow** draws 2D images: face decals (eyes, mouths, tears) into texture atlases, labelled
  storyboard sheets, contact sheets. Simple, reliable, and available inside Blender's Python.
- **Separate environments**: the host Python runs the pipeline scripts; Blender uses its own
  Python; audio tools live in one virtual environment and speech recognition in another. This
  avoids library version fights (AI libraries in particular pin very specific versions).
  **uv** creates these environments quickly.
- Keep core logic (math, motion, checks) in **plain Python with no special libraries**, so it
  runs anywhere, including inside Blender and in tests.

---

## 10. The production components (generic)

A studio pipeline built from these tools usually has these parts. Names differ between
projects; the jobs do not.

| Component | Job | Its gate |
| --- | --- | --- |
| **Script / screenplay file** | the whole film as data | — |
| **Story check** | structure: hook in the first seconds, beats, setups and payoffs, enough locations, shot variety, pacing, words per minute | structural rules pass; a separate critic scores the story |
| **Voice stage** | generate, clean, time and check every line | every take understood |
| **Set library** | locations as data (geometry, standing marks, seats, lights, time of day) shared by building, walking routes, collision checks and cameras | sets render cleanly at phone size |
| **Performance engine** | turns actions ("walk to the door", "hug Mom", "look at the photo") into poses for every frame | — |
| **Camera solver** | turns shot requests ("close-up on Leo from his left") into lens positions that keep faces visible in a vertical frame | no blocked or cropped faces |
| **Animation QC** | checks every frame for bodies inside furniture or each other, sliding or sinking feet, cameras inside walls, crossed screen direction | zero errors |
| **Storyboard** | fast render of one key frame per shot, labelled | a person (or agent looking at it) can follow the story muted |
| **Builder / renderer** | builds the scene in Blender and renders frames | all frames present |
| **Sound stage** | ambience, effects, music, mix, loudness | every line understood through the mix |
| **Delivery** | captions, encoding, technical and pacing checks, contact sheet | the file meets platform specs |
| **Review notes** | what was seen, what is still wrong, what nobody checked | written honestly |

---

## 11. Working as an agent: a short playbook

1. **Read the project's own guides first**, then this one for background.
2. **Wear one hat at a time**: writer, then director, then animator, then sound... Finish and
   check each stage before the next.
3. **Write the story before touching tools.** World rule in the first line, a hero who is the
   exception, a person who seems wrong, a turn, a reveal, a costly choice, a closing line that
   echoes an early one. Three or more locations. Short lines. Show, don't tell.
4. **Record voices early**: real line lengths decide the timing.
5. **Iterate on motion with the cheap checks** (animation QC, storyboard) until both are clean.
   Expect several rounds: move a character, change a camera side, separate two actors.
6. **Look at the pictures**: storyboard sheets, contact sheets, single frames at the key
   moments. Many problems (backs of heads, a bowed head hiding the key text, a blurry
   foreground) pass every automatic check and fail the audience.
7. **Render a small preview before the full-quality final.**
8. **Measure the delivered file**, write a review, and report plainly: what passed, the numbers,
   what is still wrong, and what needs a human (listening, watching on a phone, taste).
9. **Never publish anything** without the creator's explicit permission.

### Common time-wasters

| Symptom | Usual cause |
| --- | --- |
| "It worked" but no output | the tool failed silently (Blender without its exit-code flag) |
| Library import fails in one tool only | the wrong Python environment was active |
| Grey or black renders | missing viewport colours (Workbench), duplicate object names, missing lights |
| Things pass through each other | no per-frame collision check, or actions not staged (open arms before a hug, face the partner first) |
| Feet slide when walking | motion computed from speed instead of planted footprints |
| A face is hidden in the key shot | camera on the wrong side, another actor or hair in the way |
| Captions overlap or garble | subtitle events overlapping in time |
| A line is misheard | TTS pronunciation; respell it or change the seed |
| Film too long or too slow | estimated timing instead of real voice takes; too few cuts |
| Mix sounds weak | not normalised to −14 LUFS |
