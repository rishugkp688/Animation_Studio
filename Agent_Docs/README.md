# Agent docs

Self-contained guides for any agent (Claude, Codex, ...) starting on this project.
Together they hold enough to run the studio, and to rebuild it from nothing if the code is lost.

| File | Read it when |
| --- | --- |
| [00-tools-guide.md](00-tools-guide.md) | **Start here if you are new to these tools.** Plain-language guide to Blender, FFmpeg, captions, TTS, speech recognition, sound design and the production components, in general terms |
| [04-agent-guide.md](04-agent-guide.md) | **First for this codebase.** How to behave here, how the engine works inside, every bug already fixed, the rebuild plan |
| [03-production.md](03-production.md) | Making a film: brief, story method, screenplay format, staging, gates, delivery, review |
| [01-blender.md](01-blender.md) | Anything that touches Blender: headless runs, building, materials, faces, baking, cameras, rendering, checking |
| [02-sound.md](02-sound.md) | Voices, foley, ambience, score, mix and captions (the *One Hug Left* baseline) |
| [../OPERATOR.md](../OPERATOR.md) | **Only operating the studio?** One page: the command loop, layout errors and fixes, screenplay quick reference, reviewing without vision |
| [06-style-seam.md](06-style-seam.md) | What is Roblox-R6-specific and what is not; coupling across modules; the enforced seam; the rig-proportion experiment |
| [07-model-independence.md](07-model-independence.md) | The model/harness benchmark, its baseline results, and what was built from them (shot report, repair, catalog, schema, operator card) |
| [05-gates-reviews-retros.md](05-gates-reviews-retros.md) | What blocks delivery and why, what makes a stage stale, the two visual review checkpoints, the per-film retrospective; the Nov 2026 changes, their tests and their limits |

The department handbooks in `studio/departments/` are the short day-to-day versions; these
files are the full reference. If they disagree with the code, the code is current: fix the doc.
