# Reel editing engine

This repo turns raw phone clips into a finished 9:16 Instagram reel. The user usually
drives it from the Claude mobile app in a cloud session, so they can't run commands
themselves. Do every step for them, and send the finished video back with SendUserFile.

## Workflow (also available as `/reel`)

1. **Setup**: run `./setup.sh` once per session. If it says the Whisper model download
   is blocked, tell the user to allow `huggingface.co` in the environment's network settings.
2. **Get clips** into `input/` (files are used in name order):
   - **iCloud (fastest from an iPhone)**: Photos → select clips → Share → *Create Shared Album
     Link* → paste. `python3 reel.py fetch https://photos.icloud.com/shared/album/<id>` pulls the
     full-quality (edited, if trimmed in Photos) version of every video in the album, in order.
   - Share links (Google Drive set to "Anyone with the link", Dropbox, direct URLs):
     `python3 reel.py fetch <url> <url> ...`
   - Videos the user attached in the app: `python3 reel.py fetch /root/.claude/uploads/<session-id>/`
   - Music (optional): fetch an .mp3 into `input/` and pass `--music name.mp3` to plan.
3. **Transcribe**: `python3 reel.py transcribe` (default `small.en`; use `--model base.en` for speed).
4. **Plan**: `python3 reel.py plan --style bold --hook "Short hook title"`.
   This writes `work/edit.txt` (numbered phrases) and `work/edit.json`.
   **Act as the editor here.** Read `work/edit.txt`:
   - If a line was said more than once (retakes), keep only the LAST good take and delete the
     others from `edit.json` `segments`.
   - Delete false starts, "wait let me redo that", and off-script chatter.
   - Keep the strongest line first as the hook. Aim for 15–45 seconds total.
   - If the user gave no hook, write one of 3 to 7 words from the content, chosen to keep the most
     viewers watching: open a curiosity loop the video pays off, and keep it true to what's said.
     Don't run it through brand-voice or copy guides unless the user asks.
   - You can trim `start`/`end` and reorder segments. If a retake is inside one phrase
     ("...for me. Wait, let me redo that."), move `start`/`end` using the word times in
     `work/transcript.json`, or drop the phrase.
   - Fix misheard words (names, slang, brand names) by editing the `"w"` values in
     `work/transcript.json`. Captions are built from those words.
5. **Render**: `python3 reel.py render` → `out/reel.mp4` (**clean: no captions or hook burned in,
   which is what the user wants by default**) + `out/captions.srt`. Add `--captions` only if the user
   asks for burned-in captions on a specific reel.
   **Pause cutting (the user's top priority: no dead air, but never clipped words).** Render
   shortens every pause over 0.35s (`--max-silence`) to a ~0.2s beat and trims phrase edges, so
   joins between phrases are one natural beat. A frame counts as silence only if it's >25 dB
   under speech, or 15–25 dB under AND the VAD hears no speech. 30ms+ within 20 dB of speech is
   never cut (soft words). Edges inside a word that's mostly outside a hand-picked range are
   dropped, not kept as fragments. Check the `pause check` line, and only hand-trim a pause if
   it's clearly dead air; leave hesitations and breaths alone.
   **Light:** each clip's brightness is measured and one of `LIGHT` is applied automatically
   (none / brighten / brighten_more / darken / darken_more). Override with `"light"` in edit.json
   (one preset or `{clip: preset}`).
6. **Check**: grab 2–3 frames (`ffmpeg -ss 2 -i out/reel.mp4 -frames:v 1 -vf scale=360:-1 work/f.png`)
   and look at them for framing (and, with `--captions`, that captions don't cover the face).
7. **Deliver**: SendUserFile only `out/reel.mp4` (the clean version). They add captions in
   Edits/CapCut themselves. Still suggest a hook line in the chat message so they can type it in.
   Paste `out/storyboard.md` (kept lines, pauses shortened, light setting) into the chat as the
   storyboard, plus a 1–2 line summary of retakes and lines you cut. Revisions: edit `work/edit.json` and run `render` again.
   You don't need to transcribe again.

## Caption styles (`--style`)
- `bold`: big uppercase Anton, 3 words at a time, the spoken word turns yellow.
- `cool`: yellow serif italic, one word at a time (the soft "oh," look).
- `clean`: Montserrat on a dark box, up to 5 words, spoken word highlighted.
Positions, sizes and colours are defined in `STYLES` in `reel.py`.

## CapCut handoff
The default render is already clean. In CapCut desktop you can import `out/captions.srt`. The audio is already loudness-normalised to −14 LUFS.

## Notes
- iPhone HDR footage is tone-mapped to SDR automatically (otherwise it looks washed out on IG).
- Fonts live in `fonts/` (OFL-licensed: Anton, Montserrat, DM Serif Display).
- `input/`, `work/` and `out/` are git-ignored. Never commit user videos.
