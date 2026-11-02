---
name: reel
description: Edit raw phone clips into a finished Instagram reel (cuts, captions, sound). Use when the user shares video clips or links and asks for a reel, edit, or captions.
---

Follow the workflow in CLAUDE.md at the repo root, from setup through delivery.

Arguments the user may give after `/reel`: share links to clips, a caption style
(bold / cool / clean), a hook line, or music. If they gave no clips, ask them to either
attach the videos in the app or paste Google Drive / Dropbox links set to "Anyone with the link".

Always end by sending `out/reel.mp4` with SendUserFile, plus a short note on what was cut.
