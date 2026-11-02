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
   - If the user gave no hook, write a punchy one of 3 to 7 words from the content.
   - You can trim `start`/`end` and reorder segments. If a retake is inside one phrase
     ("...for me. Wait, let me redo that."), move `start`/`end` using the word times in
     `work/transcript.json`, or drop the phrase.
   - Fix misheard words (names, slang, brand names) by editing the `"w"` values in
     `work/transcript.json`. Captions are built from those words.
5. **Render**: `python3 reel.py render` → `out/reel.mp4` + `out/captions.srt`.
6. **Check**: grab 2–3 frames (`ffmpeg -ss 2 -i out/reel.mp4 -frames:v 1 -vf scale=360:-1 work/f.png`)
   and look at them to make sure captions aren't covering the face and aren't off-screen.
7. **Deliver**: SendUserFile `out/reel.mp4`, and `out/captions.srt` if they use CapCut.
   Summarise in 2–3 lines what you cut. Revisions: edit `work/edit.json` and run `render` again.
   You don't need to transcribe again.

## Caption styles (`--style`)
- `bold`: big uppercase Anton, 3 words at a time, the spoken word turns yellow.
- `cool`: yellow serif italic, one word at a time (the soft "oh," look).
- `clean`: Montserrat on a dark box, up to 5 words, spoken word highlighted.
Positions, sizes and colours are defined in `STYLES` in `reel.py`.

## CapCut handoff
`python3 reel.py render --no-captions -o reel_clean.mp4` gives a clean cut. In CapCut,
import it together with `out/captions.srt`. The audio is already loudness-normalised to −14 LUFS.

## Notes
- iPhone HDR footage is tone-mapped to SDR automatically (otherwise it looks washed out on IG).
- Fonts live in `fonts/` (OFL-licensed: Anton, Montserrat, DM Serif Display).
- `input/`, `work/` and `out/` are git-ignored. Never commit user videos.
