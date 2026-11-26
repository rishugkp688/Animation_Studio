# Reference reels

Third-party Roblox story reels the studio's format was studied from. The videos stay local
(git-ignored) and are never reused in our films; only the lessons are.

What we took from them is written into the handbooks:

- story shape (world rule in line one, "...except me", the person who seems wrong, the
  costly choice, the reframing last line): [01-story](../studio/departments/01-story.md)
- pacing numbers (13–41 cuts/min, median shot 1.3–4.2 s, no frozen seconds):
  [`studio/quality-bar.json`](../studio/quality-bar.json), checked by `scripts/video_audit.py`
- captions (1–3 bold outlined words, highlighted active word, lower middle):
  [05-post](../studio/departments/05-post.md)

To measure a reel against the bar: `python3 scripts/video_audit.py "guides/<file>.mp4"`.
