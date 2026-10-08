"""Burn subtitles into a video: draw each cue as a transparent PNG (Pillow), overlay with ffmpeg."""
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_STYLE = {
    "font": "Arial", "size": 4.6, "bold": False, "color": "#ffffff",
    "box": True, "box_opacity": 0.75, "outline": False,
    "position": "bottom", "margin": 6.0,
}

# Windows font files per family (regular, bold); Linux fallbacks for testing.
FONT_FILES = {
    "Arial": ("arial.ttf", "arialbd.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf"),
}
FALLBACK = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")


class Cancelled(Exception):
    pass


_running = {}  # ffmpeg process -> its unfinished output file


def abort_all():
    """On quit: stop ffmpeg (it would keep running on its own) and remove its unfinished file."""
    for proc, tmp in list(_running.items()):
        try:
            proc.kill()
            proc.wait(5)
        except Exception:
            pass
        Path(tmp).unlink(missing_ok=True)


def style_of(s):
    return {**DEFAULT_STYLE, **(s or {})}


def _font_dirs():
    dirs = []
    win = os.environ.get("WINDIR")
    if win:
        dirs.append(Path(win) / "Fonts")
    la = os.environ.get("LOCALAPPDATA")
    if la:
        dirs.append(Path(la) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path("/usr/share/fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    return [d for d in dirs if d.exists()]


def _find_font_file(name):
    for d in _font_dirs():
        p = d / name
        if p.exists():
            return p
        for q in d.rglob("*"):
            if q.name.lower() == name.lower():
                return q
    return None


def load_font(family, bold, px):
    from PIL import ImageFont
    reg, bld = FONT_FILES.get(family, FONT_FILES["Arial"])
    for name in ((bld if bold else reg), reg, FALLBACK[1 if bold else 0], FALLBACK[0]):
        f = _find_font_file(name)
        if f:
            return ImageFont.truetype(str(f), px)
    return ImageFont.load_default(px)


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (255, 255, 255)


def render_cue(text, W, H, st, pos=None, font=None):
    """Return an RGBA image W x H with the cue drawn the same way the preview shows it."""
    from PIL import Image, ImageDraw
    px = max(10, round(st["size"] / 100 * H))
    font = font or load_font(st["font"], st["bold"], px)
    lines = [l for l in text.split("\n") if l.strip()]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if not lines:
        return img
    d = ImageDraw.Draw(img)
    ascent, descent = font.getmetrics()
    lh = round((ascent + descent) * 1.08)
    padx, pady = round(px * 0.32), round(px * 0.06)
    total = len(lines) * (lh + 2 * pady)
    margin = round(st["margin"] / 100 * H)
    top = margin if (pos or st["position"]) == "top" else H - margin - total
    color = _rgb(st["color"]) + (255,)
    box = (0, 0, 0, round(255 * float(st["box_opacity"])))
    stroke = max(1, px // 12) if st["outline"] else 0
    y = top
    for line in lines:
        w = d.textlength(line, font=font)
        x = (W - w) / 2
        if st["box"]:
            d.rectangle([x - padx, y, x + w + padx, y + lh + 2 * pady], fill=box)
        d.text((x, y + pady), line, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += lh + 2 * pady
    return img


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _encoder(ff):
    out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    for enc, args in (("libx264", ["-preset", "veryfast", "-crf", "19"]),
                      ("h264_mf", ["-b:v", "8M"]),
                      ("libopenh264", ["-b:v", "8M"])):
        if re.search(rf"\s{enc}\s", out):
            return enc, args
    return "mpeg4", ["-q:v", "3"]


def video_geometry(path):
    import av
    with av.open(str(path)) as c:
        v = next((s for s in c.streams if s.type == "video"), None)
        if v is None:
            raise ValueError("no video stream")
        W, H = v.codec_context.width, v.codec_context.height
        rot = 0
        try:
            rot = int(float(v.metadata.get("rotate", 0)))
        except (TypeError, ValueError):
            pass
        try:
            for sd in getattr(v, "side_data", None) or []:
                if "matrix" in str(getattr(sd, "type", "")).lower():
                    rot = int(round(float(getattr(sd, "rotation", rot) or rot)))
        except Exception:
            pass
        if abs(rot) % 180 == 90:
            W, H = H, W
        dur = (c.duration or 0) / 1_000_000 or float(v.duration * v.time_base if v.duration else 0)
    return W, H, dur


def out_path_for(video):
    p = Path(video)
    target = p.with_name(p.stem + "_tekstet.mp4")
    k = 2
    while target.exists():
        target = p.with_name(f"{p.stem}_tekstet ({k}).mp4")
        k += 1
    return target


def burn(video, cues, style, workdir, on_progress, stop):
    """Render cues into a copy of the video. Returns the output path.
    Whatever happens (done, cancelled, error), the temp folder and any unfinished file are removed."""
    work = Path(workdir)
    try:
        return _burn(video, cues, style, work, on_progress, stop)
    finally:
        for _ in range(10):  # ffmpeg may hold the PNGs for a moment after it exits (Windows)
            shutil.rmtree(work, ignore_errors=True)
            if not work.exists():
                break
            time.sleep(0.2)


def _burn(video, cues, style, work, on_progress, stop):
    st = style_of(style)
    W, H, dur = video_geometry(video)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    from PIL import Image
    font = load_font(st["font"], st["bold"], max(10, round(st["size"] / 100 * H)))
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(work / "blank.png")
    entries, t = [], 0.0
    cues = sorted((c for c in cues if c["text"].strip()), key=lambda c: c["start"])
    for i, c in enumerate(cues):
        if stop.is_set():
            raise Cancelled()
        start = max(c["start"], t)
        if start > t + 0.001:
            entries.append(("blank.png", start - t))
        if c["end"] <= start:
            continue
        name = f"c{i:05d}.png"
        render_cue(c["text"], W, H, st, c.get("pos"), font).save(work / name, optimize=False, compress_level=1)
        entries.append((name, c["end"] - start))
        t = c["end"]
        on_progress(0.15 * (i + 1) / max(1, len(cues)))
    entries.append(("blank.png", max(1.0, dur - t + 1)))
    lines = ["ffconcat version 1.0"]
    for name, d in entries:
        lines += [f"file '{name}'", f"duration {d:.3f}"]
    lines.append("file 'blank.png'")
    (work / "list.ffconcat").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ff = ffmpeg_exe()
    enc, enc_args = _encoder(ff)
    target = out_path_for(video)
    tmp = target.with_name(target.stem + ".part.mp4")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(work / "list.ffconcat"),
           "-filter_complex", "[1:v]format=rgba[s];[0:v][s]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[v]",
           "-map", "[v]", "-map", "0:a?", "-c:v", enc, *enc_args, "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(tmp)]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
    _running[proc] = tmp
    try:
        err_lines = []
        threading.Thread(target=lambda: err_lines.extend(proc.stderr.readlines()), daemon=True).start()
        # Cancel is checked by a watcher too, so it works even while ffmpeg prints nothing
        def watch():
            while proc.poll() is None:
                if stop.wait(0.5):
                    proc.kill()
                    return

        threading.Thread(target=watch, daemon=True).start()
        for line in proc.stdout:
            if stop.is_set():
                break
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=")[1])
                    if dur:
                        on_progress(0.15 + 0.85 * min(1.0, us / 1e6 / dur))
                except ValueError:
                    pass
        proc.wait()
        if stop.is_set():
            raise Cancelled()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg: " + "".join(err_lines[-3:]).strip()[:300])
        tmp.replace(target)
        return target
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for _ in range(10):  # the unfinished file; Windows may keep it locked a moment after ffmpeg exits
            try:
                tmp.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(0.2)
        raise
    finally:
        _running.pop(proc, None)
