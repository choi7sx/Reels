#!/usr/bin/env python3
"""Reel editing engine: raw phone clips -> finished 9:16 Instagram reel.

Pipeline (each step writes a file the next step reads, so Claude can edit in between):

  fetch       share links / app uploads      -> input/
  transcribe  input/*.mp4|mov                -> work/transcript.json  (word timestamps)
  plan        transcript                     -> work/edit.json + work/edit.txt (phrases to keep)
  render      edit.json                      -> out/reel.mp4 (+ out/captions.srt)

Run `python3 reel.py all` to do transcribe+plan+render in one go.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT, WORK, OUT, FONTS = ROOT / "input", ROOT / "work", ROOT / "out", ROOT / "fonts"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
MUSIC_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}
W, H, FPS = 1080, 1920, 30
FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm"}

# ---------------------------------------------------------------- caption looks
# Colours are ASS &HBBGGRR (blue-green-red).
STYLES = {
    # Big uppercase Anton, 3 words at a time, the spoken word turns yellow.
    "bold": dict(font="Anton", size=125, upper=True, max_words=3, max_chars=18,
                 base="&H00FFFFFF", active="&H0000E6FF", outline=6, shadow=0,
                 border_style=1, pos=(540, 1240), highlight=True, pop=True),
    # Elegant serif italic, one word at a time, warm yellow (like the "oh," look).
    "cool": dict(font="DM Serif Display", size=150, upper=False, max_words=1, max_chars=14,
                 base="&H0080E8FF", active="&H0080E8FF", outline=0, shadow=3,
                 border_style=1, pos=(540, 1180), highlight=False, pop=True, italic=True),
    # Clean Montserrat on a dark box, up to 5 words, active word highlighted.
    "clean": dict(font="Montserrat ExtraBold", size=64, upper=False, max_words=5, max_chars=26,
                  base="&H00FFFFFF", active="&H0055D4FF", outline=14, shadow=0,
                  border_style=3, back="&H66000000", pos=(540, 1300), highlight=True, pop=False),
}


def run(cmd, quiet=True):
    print("  $", " ".join(str(c) for c in cmd)[:220] + (" ..." if len(" ".join(map(str, cmd))) > 220 else ""))
    r = subprocess.run([str(c) for c in cmd], capture_output=quiet, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed ({r.returncode}):\n{(r.stderr or '')[-3000:]}")
    return r


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def clips():
    files = sorted(p for p in INPUT.iterdir() if p.suffix.lower() in VIDEO_EXT) if INPUT.exists() else []
    if not files:
        sys.exit(f"No video clips in {INPUT}/ - add some (see `reel.py fetch -h`).")
    return files


# ---------------------------------------------------------------- fetch
def direct_url(url):
    """Turn common share links into direct-download links."""
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url)
    if m:
        return f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    if "dropbox.com" in url:
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        q["dl"] = "1"
        return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))
    return url


def cmd_fetch(a):
    INPUT.mkdir(exist_ok=True)
    n = len(list(INPUT.iterdir()))
    for src in a.sources:
        if src.startswith(("http://", "https://")):
            n += 1
            tmp = INPUT / f"clip{n:02d}.download"
            run(["curl", "-fL", "--retry", "3", "-o", tmp, direct_url(src)], quiet=False)
            kind = subprocess.run(["file", "-b", "--mime-type", str(tmp)], capture_output=True, text=True).stdout.strip()
            if not (kind.startswith("video/") or kind.startswith("audio/") or kind == "application/octet-stream"):
                tmp.unlink()
                sys.exit(f"{src} did not download as a video (got {kind}). Is the link shared as 'Anyone with the link'?")
            ext = ".mp3" if kind.startswith("audio/") else ".mp4" if "mp4" in kind else ".mov"
            tmp.rename(tmp.with_suffix(ext))
            print(f"  -> {tmp.with_suffix(ext).name}")
        else:
            p = Path(src)
            files = [f for f in sorted(p.rglob("*")) if f.suffix.lower() in VIDEO_EXT | MUSIC_EXT] if p.is_dir() else [p]
            for f in files:
                shutil.copy2(f, INPUT / f.name)
                print(f"  -> {f.name}")


# ---------------------------------------------------------------- transcribe
def load_audio(path):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version issues)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"could not read audio from {path.name}: {r.stderr.decode()[-500:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def cmd_transcribe(a):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper missing: run ./setup.sh")
    WORK.mkdir(exist_ok=True)
    try:
        model = WhisperModel(a.model, device="cpu", compute_type="int8")
    except Exception as e:  # usually the model download being blocked
        sys.exit(f"Could not load Whisper model '{a.model}': {e}\n"
                 "The model downloads from huggingface.co once - allow that domain in the "
                 "environment's network settings.")
    out = {"model": a.model, "clips": []}
    for f in clips():
        print(f"transcribing {f.name} ...")
        segs, info = model.transcribe(load_audio(f), word_timestamps=True, vad_filter=False,
                                      language=a.language, condition_on_previous_text=False)
        words = []
        for s in segs:
            for w in s.words or []:
                words.append({"w": w.word.strip(), "start": round(w.start, 3),
                              "end": round(w.end, 3), "p": round(w.probability, 3)})
        dur = float(probe(f).get("format", {}).get("duration", 0))
        out["clips"].append({"file": f.name, "duration": dur, "words": words})
        print(f"  {len(words)} words, {dur:.1f}s: " + " ".join(w["w"] for w in words)[:160])
    (WORK / "transcript.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {WORK/'transcript.json'}")


# ---------------------------------------------------------------- plan
def norm(w):
    return re.sub(r"[^\w']", "", w.lower())


def cmd_plan(a):
    t = json.loads((WORK / "transcript.json").read_text())
    segments = []
    for c in t["clips"]:
        words = [w for w in c["words"] if not (a.drop_fillers and norm(w["w"]) in FILLERS)]
        phrase = []
        for w in words:
            if phrase and (w["start"] - phrase[-1]["end"] > a.max_gap):
                segments.append(phrase)
                phrase = []
            phrase.append({**w, "clip": c["file"], "clip_dur": c["duration"]})
        if phrase:
            segments.append(phrase)
    plan = {
        "style": a.style,
        "hook": a.hook or "",           # optional title shown at the top for the first seconds
        "hook_seconds": 3.0,
        "music": a.music or "",         # filename in input/ (optional)
        "music_volume": 0.12,
        "segments": [],
    }
    lines = []
    for i, ph in enumerate(segments):
        start = max(0.0, ph[0]["start"] - a.pad_in)
        end = min(ph[-1]["clip_dur"] or 1e9, ph[-1]["end"] + a.pad_out)
        text = " ".join(w["w"] for w in ph)
        plan["segments"].append({"id": i, "clip": ph[0]["clip"], "start": round(start, 3),
                                 "end": round(end, 3), "text": text})
        lines.append(f"[{i:>3}] {ph[0]['clip']} {start:7.2f}-{end:7.2f}  {text}")
    (WORK / "edit.json").write_text(json.dumps(plan, indent=1))
    (WORK / "edit.txt").write_text("\n".join(lines) + "\n")
    total = sum(s["end"] - s["start"] for s in plan["segments"])
    print("\n".join(lines))
    print(f"\n{len(lines)} phrases, {total:.1f}s total -> {WORK/'edit.json'}")


# ---------------------------------------------------------------- render
def ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_escape(s):
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_captions(timeline_words, style_name, hook, hook_seconds):
    st = STYLES[style_name]
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{st['base']},{st['base']},{st.get('back', '&H00000000') if st['border_style'] == 3 else '&H00000000'},&H80000000,0,{1 if st.get('italic') else 0},0,0,100,100,0,0,{st['border_style']},{st['outline']},{st['shadow']},5,80,80,0,1
Style: Hook,Montserrat ExtraBold,66,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,18,0,8,90,90,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_seconds)},Hook,,0,0,0,,{ass_escape(hook)}")

    # group words into on-screen chunks
    chunks, cur = [], []
    for w in timeline_words:
        txt = w["w"].upper() if st["upper"] else w["w"]
        if cur and (len(cur) >= st["max_words"]
                    or len(" ".join(x["t"] for x in cur + [{"t": txt}])) > st["max_chars"]
                    or w["start"] - cur[-1]["end"] > 0.6
                    or w.get("cut_before")):
            chunks.append(cur)
            cur = []
        cur.append({"t": txt, "start": w["start"], "end": w["end"]})
        if re.search(r"[.!?]$", txt):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    x, y = st["pos"]
    srt = []
    for ci, ch in enumerate(chunks):
        c_start = ch[0]["start"]
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else ch[-1]["end"] + 0.4
        c_end = min(nxt, ch[-1]["end"] + 0.5)
        srt.append((c_start, c_end, " ".join(w["t"] for w in ch)))
        pop = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}" if st["pop"] else ""
        if not st["highlight"]:
            ev.append(f"Dialogue: 0,{ass_time(c_start)},{ass_time(c_end)},Cap,,0,0,0,,"
                      f"{{\\pos({x},{y})}}{pop}{ass_escape(' '.join(w['t'] for w in ch))}")
            continue
        for wi, w in enumerate(ch):
            s = w["start"] if wi else c_start
            e = ch[wi + 1]["start"] if wi + 1 < len(ch) else c_end
            parts = []
            for wj, w2 in enumerate(ch):
                t = ass_escape(w2["t"])
                parts.append(f"{{\\c{st['active']}}}{t}{{\\c{st['base']}}}" if wj == wi else t)
            p = pop if wi == 0 else ""
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{\\pos({x},{y})}}{p}{' '.join(parts)}")
    return hdr + "\n".join(ev) + "\n", srt


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def video_filter(path):
    """Scale/crop to 1080x1920 and tone-map iPhone HDR (HLG/PQ) to normal SDR."""
    v = next((s for s in probe(path).get("streams", []) if s.get("codec_type") == "video"), {})
    chain = []
    if v.get("color_transfer") in ("arib-std-b67", "smpte2084"):
        chain.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                     "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    chain.append(f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}")
    chain.append(f"fps={FPS},setsar=1,format=yuv420p")
    return ",".join(chain)


def cmd_render(a):
    plan = json.loads((WORK / "edit.json").read_text())
    t = json.loads((WORK / "transcript.json").read_text())
    words_by_clip = {c["file"]: c["words"] for c in t["clips"]}
    style = a.style or plan.get("style", "bold")
    if style not in STYLES:
        sys.exit(f"unknown style {style}; pick one of {', '.join(STYLES)}")
    seg_dir = WORK / "segments"
    shutil.rmtree(seg_dir, ignore_errors=True)
    seg_dir.mkdir(parents=True)
    OUT.mkdir(exist_ok=True)

    timeline_words, offset, parts = [], 0.0, []
    filters = {}
    for i, s in enumerate(plan["segments"]):
        src = INPUT / s["clip"]
        if src not in filters:
            filters[src] = video_filter(src)
        dur = s["end"] - s["start"]
        part = seg_dir / f"{i:03d}.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start']:.3f}", "-i", src, "-t", f"{dur:.3f}",
             "-vf", filters[src], "-af", "aresample=48000,aformat=channel_layouts=stereo,"
             "afade=t=in:d=0.02,afade=t=out:st=%.3f:d=0.03" % max(0, dur - 0.03),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "pcm_s16le", "-ar", "48000",
             "-video_track_timescale", "30000", part.with_suffix(".mov")])
        parts.append(part.with_suffix(".mov"))
        first = True
        for w in words_by_clip.get(s["clip"], []):
            if w["start"] >= s["start"] - 0.05 and w["end"] <= s["end"] + 0.05 and \
                    not (a.drop_fillers and norm(w["w"]) in FILLERS):
                timeline_words.append({"w": w["w"], "start": offset + max(0, w["start"] - s["start"]),
                                       "end": offset + min(dur, w["end"] - s["start"]), "cut_before": first})
                first = False
        # use the real encoded length so captions never drift
        offset += float(probe(parts[-1])["format"]["duration"])

    concat = seg_dir / "list.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = WORK / "joined.mov"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", joined])

    ass, srt = build_captions(timeline_words, style, plan.get("hook", ""), plan.get("hook_seconds", 3))
    (WORK / "captions.ass").write_text(ass)
    (OUT / "captions.srt").write_text("".join(
        f"{i + 1}\n{srt_time(s)} --> {srt_time(e)}\n{txt}\n\n" for i, (s, e, txt) in enumerate(srt)))

    music = plan.get("music") or ""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", joined]
    if music:
        cmd += ["-stream_loop", "-1", "-i", INPUT / music]
    vf = "null" if a.no_captions else f"ass={WORK/'captions.ass'}:fontsdir={FONTS}"
    voice = "[0:a]highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=120[v]"
    if music:
        af = (f"{voice};[v]asplit[v1][v2];[1:a]volume={plan.get('music_volume', 0.12)}[m];"
              "[m][v1]sidechaincompress=threshold=0.05:ratio=6:release=400[md];"
              "[v2][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        af = f"{voice};[v]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    out = OUT / (a.output or "reel.mp4")
    cmd += ["-filter_complex", f"[0:v]{vf}[vo];{af}", "-map", "[vo]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-r", str(FPS), "-movflags", "+faststart", "-t", f"{offset:.3f}", out]
    run(cmd)
    size = out.stat().st_size / 1e6
    print(f"\nDONE  {out}  ({offset:.1f}s, {size:.1f} MB, style={style})\n      {OUT/'captions.srt'} (import into CapCut)")


# ---------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download share links (Google Drive/Dropbox/direct) or copy local files into input/")
    f.add_argument("sources", nargs="+")
    f.set_defaults(fn=cmd_fetch)

    def add_transcribe(sp):
        sp.add_argument("--model", default="small.en", help="whisper model: base.en (fast), small.en, medium.en (best)")
        sp.add_argument("--language", default=None)

    def add_plan(sp):
        sp.add_argument("--style", default="bold", choices=list(STYLES))
        sp.add_argument("--hook", default="", help="title text shown at the top for the first 3s")
        sp.add_argument("--music", default="", help="music filename inside input/")
        sp.add_argument("--max-gap", type=float, default=0.45, help="pauses longer than this (s) are cut")
        sp.add_argument("--pad-in", type=float, default=0.08)
        sp.add_argument("--pad-out", type=float, default=0.15)
        sp.add_argument("--keep-fillers", dest="drop_fillers", action="store_false")

    def add_render(sp):
        sp.add_argument("--no-captions", action="store_true", help="clean video (add captions in CapCut with captions.srt)")
        sp.add_argument("-o", "--output", default="")

    t = sub.add_parser("transcribe"); add_transcribe(t); t.set_defaults(fn=cmd_transcribe)
    pl = sub.add_parser("plan"); add_plan(pl); pl.set_defaults(fn=cmd_plan)
    r = sub.add_parser("render"); add_render(r)
    r.add_argument("--style", default="", choices=[""] + list(STYLES))
    r.add_argument("--keep-fillers", dest="drop_fillers", action="store_false")
    r.set_defaults(fn=cmd_render)

    al = sub.add_parser("all", help="transcribe + plan + render")
    add_transcribe(al); add_plan(al); add_render(al)

    def run_all(a):
        cmd_transcribe(a); cmd_plan(a)
        a.style = ""  # render uses the style saved in edit.json
        cmd_render(a)
    al.set_defaults(fn=run_all)

    a = p.parse_args()
    os.chdir(ROOT)
    a.fn(a)


if __name__ == "__main__":
    main()
