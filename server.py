"""Teksting - local subtitle app. Run via start.bat (Windows) or: python server.py"""
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")              # plain downloads -> measurable progress
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import json
import mimetypes
import socket
import subprocess
import sys
import threading
import time

T0 = time.time()  # for the startup timings in the log
import traceback
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from tekst import burn, compare, segment
from tekst.engine import MODELS, PEAK_RATE, Cancelled, Engine, compute_peaks, media_duration
from tekst.spell import Speller
from tekst.learn import Learner
from tekst.store import Store

APP_DIR = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TEKSTING_DATA", APP_DIR / "data"))
os.environ.setdefault("HF_HOME", str(DATA / "hf-cache"))
PORT = int(os.environ.get("TEKSTING_PORT", "8765"))

store = Store(DATA)
engine = Engine(DATA / "models")
speller = Speller(DATA / "dictionaries")
learner = Learner(DATA / "corrections.json")
app = FastAPI()


# ------------------------------------------------------------------ worker ---
def keep_awake(on):
    """Stop Windows from sleeping while a job runs (only affects this thread)."""
    if os.name == "nt":
        import ctypes
        flags = 0x80000000 | (0x00000001 if on else 0)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


def hint_text(settings):
    """Glossary terms + learned corrections, given to the model as hotwords."""
    if not settings.get("hotwords", True):
        return None
    seen, terms = set(), []
    for t in store.glossary_terms() + learner.hint_words():
        if t.lower() not in seen:
            seen.add(t.lower())
            terms.append(t)
    return ", ".join(terms)[:600] or None


def allow_set():
    return store.glossary_allow() | frozenset(w.lower() for t in learner.hint_words() for w in t.split())


def finish_cues(pid, words, settings):
    """Segment, apply learned corrections, store as the new baseline. Returns cues."""
    p = segment.params(settings["rules"], collapse_repeats=settings.get("collapse_repeats", False))
    cues = segment.make_cues(words, p)
    fixes = learner.apply(cues, wrap=lambda t: segment.wrap_text(t, p), max_chars=p.max_chars)
    store.save_cues(pid, cues, edited=False)
    store.save_json(pid, "cues_orig.json", cues)
    store.save_json(pid, "autofix.json", fixes)
    return cues, fixes


class Tracker:
    """Turns phase reports from the engine into overall progress, speed and time left.

    Each job has a plan: [(status, phase, expected seconds)]. Progress is time-weighted, so 50 %
    means about half the time. While text is being produced we measure the real speed and use it
    for the rest of the job. The UI gets {progress, rate, cap, pt} and moves the bar smoothly
    between reports instead of jumping every 30-60 s.
    """

    def __init__(self, pid, plan, scale=1.0):
        self.pid, self.scale = pid, scale
        total = sum(p[2] for p in plan) or 1.0
        self.ranges, self.order, acc = {}, [], 0.0
        for st, ph, sec in plan:
            self.ranges[(st, ph)] = (acc / total, (acc + sec) / total, sec)
            self.order.append((st, ph))
            acc += sec
        self.cur, self.first, self.last_write = None, None, 0.0

    def reporter(self, status):
        return lambda p, phase, cap=None: self.report(status, phase, p, cap)

    def report(self, status, phase, p, cap=None):
        key = (status, phase)
        if key not in self.ranges:
            return
        lo, hi, sec = self.ranges[key]
        now = time.time()
        changed = key != self.cur
        if changed:
            self.cur, self.first = key, (now, p)
        elif now - self.last_write < 0.5 and p < 1.0:
            return
        self.last_write = now
        total = lo + (hi - lo) * p
        cap_total = lo + (hi - lo) * (1.0 if cap is None else max(p, cap))
        rate = (hi - lo) / max(1.0, sec * self.scale)              # expected progress per second
        t1, p1 = self.first
        if phase == "decode" and p > p1 and now - t1 > 8:
            measured = (hi - lo) * (p - p1) / (now - t1)
            rate = measured
            self.scale = min(4.0, max(0.25, ((hi - lo) / sec) / measured))  # applies to later phases too
        # time left: rest of this phase at the current rate + later phases at the (corrected) estimate
        later = self.order[self.order.index(key) + 1:]
        eta = (hi - total) / rate + sum(self.ranges[k][2] for k in later) * self.scale
        store.update(self.pid, status=status, phase=phase, progress=round(total, 5), cap=round(cap_total, 5),
                     rate=rate, pt=now, eta=eta)


def time_scale():
    """How much slower/faster this PC is than our built-in estimates (from finished jobs)."""
    r = sorted(m["took"] / m["expected"] for m in store.all()
               if m.get("status") == "done" and m.get("took") and m.get("expected"))[-9:]
    return min(4.0, max(0.25, r[len(r) // 2])) if r else 1.0


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.wake = threading.Event()
        self.stop_job = threading.Event()
        self.paused = False
        self.current = None
        self.downloading = {}  # model -> progress (0-1)

    def run(self):
        while True:
            job = None if self.paused else next((m for m in store.all() if m["status"] == "queued"), None)
            if job is None:
                keep_awake(False)
                self.wake.wait(2)
                self.wake.clear()
                continue
            keep_awake(True)
            self.current = job["id"]
            self.stop_job.clear()
            try:
                self.process(job)
            except Cancelled:
                store.update(job["id"], status="cancelled", eta=None, message="")
            except Exception as e:
                traceback.print_exc()
                store.update(job["id"], status="error", eta=None, message=str(e)[:300])
            finally:
                self.current = None
                self.downloading.clear()

    def ensure_model(self, pid, name):
        """Download the model if needed. Returns False (and marks the job) if it failed."""
        if engine.is_ready(name):
            return True
        store.update(pid, status="downloading", progress=0.0, message="", error_kind=None, dl_model=name)

        def dl(p):
            self.downloading[name] = p
            store.update(pid, progress=p)

        try:
            engine.download(name, dl, self.stop_job)
        except Cancelled:
            raise
        except Exception as e:
            traceback.print_exc()
            store.update(pid, status="error", error_kind="download", eta=None, message=str(e)[:200])
            return False
        finally:
            self.downloading.pop(name, None)
        return True

    def process(self, job):
        pid, name = job["id"], job["model"]
        if not Path(job["path"]).exists():
            store.update(pid, status="error", error_kind="missing", message=job["path"])
            return
        settings = store.settings()
        double = bool(settings.get("double_check")) and name != "small"
        if not self.ensure_model(pid, name):
            return
        if double and not self.ensure_model(pid, "small"):
            return
        prompt = hint_text(settings)
        t0 = time.time()
        dur = job.get("duration") or media_duration(job["path"]) or 600.0
        plan = [("transcribing", ph, sec) for ph, sec in engine.plan(name, dur, rescue=True)]
        if double:
            plan += [("checking", ph, sec) for ph, sec in engine.plan("small", dur, loaded=False)]
        plan.append(("finishing", "finish", dur / 120 + 2))
        tr = Tracker(pid, plan, time_scale())
        store.update(pid, status="transcribing", phase=None, progress=0.0, eta=None, rate=0, pt=None, double=double,
                     expected=sum(p[2] for p in plan))
        words = engine.transcribe(name, job["path"], job["language"], prompt,
                                  tr.reporter("transcribing"), self.stop_job, rescue=True)
        store.save_words(pid, words)
        rescued = []
        for w in words:
            if w.get("r"):
                if rescued and w["s"] - rescued[-1]["e"] < 1.0:
                    rescued[-1]["e"] = w["e"]
                else:
                    rescued.append({"s": w["s"], "e": w["e"]})
        store.save_json(pid, "rescued.json", rescued)
        cues, fixes = finish_cues(pid, words, settings)
        disagree = []
        if double:
            alt = engine.transcribe("small", job["path"], job["language"], prompt,
                                    tr.reporter("checking"), self.stop_job)
            allow = allow_set()
            is_word = lambda w: not speller.unknown(w, job["language"], allow)  # noqa: E731
            disagree = compare.disagreements(words, alt, is_word)
            # recovered stretches are flagged already; don't flag them twice
            disagree = [d for d in disagree if not any(r["s"] - 0.3 <= d["s"] and d["e"] <= r["e"] + 0.3 for r in rescued)]
        store.save_json(pid, "disagree.json", disagree)
        tr.report("finishing", "finish", 0.0)
        try:
            peaks_for(pid)
        except Exception:
            traceback.print_exc()
        store.update(pid, status="done", progress=1.0, eta=None, message="", finished=time.time(),
                     took=time.time() - t0, disagree=len(disagree), rescued=len(rescued),
                     autofixed=len(fixes), edit_rate=0.0, work_seconds=0)


class Exporter(threading.Thread):
    """Burns subtitles into videos, one at a time, separate from transcription."""

    def __init__(self):
        super().__init__(daemon=True)
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.current = None

    def run(self):
        while True:
            job = next((m for m in store.all() if (m.get("export") or {}).get("status") == "queued"), None)
            if job is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            pid = job["id"]
            self.current = pid
            self.stop.clear()
            last = [0.0]

            def prog(p):
                if time.time() - last[0] > 0.7 or p >= 1:
                    last[0] = time.time()
                    store.update(pid, export={"status": "running", "progress": p})

            try:
                store.update(pid, export={"status": "running", "progress": 0.0})
                out = burn.burn(job["path"], store.cues(pid), store.settings()["style"],
                                store._dir(pid) / "burn", prog, self.stop)
                store.update(pid, export={"status": "done", "progress": 1.0, "path": str(out), "at": time.time()})
            except burn.Cancelled:
                store.update(pid, export={"status": "cancelled"})
            except Exception as e:
                traceback.print_exc()
                store.update(pid, export={"status": "error", "message": str(e)[:300]})
            finally:
                self.current = None


def peaks_for(pid):
    f = store._dir(pid) / "peaks.bin"
    if not f.exists():
        f.write_bytes(compute_peaks(store.get(pid)["path"]))
    return f


worker = Worker()
exporter = Exporter()


# --------------------------------------------------------------- helpers ---
def state():
    jobs = store.all()
    return {"settings": store.settings(), "models": engine.status(), "jobs": jobs,
            "paused": worker.paused, "current": worker.current, "data_dir": str(DATA),
            "glossary": store.glossary(), "now": time.time(), "time_scale": time_scale()}


def project(pid):
    try:
        return store.get(pid)
    except KeyError:
        raise HTTPException(404, "Project not found")


PICKER = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
# Tell Windows we draw at the real screen resolution; otherwise it upscales the dialog (blurry).
try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor (Win 8.1+)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()        # older Windows
except Exception:
    pass
r = tk.Tk(); r.withdraw(); r.attributes("-topmost", True); r.update()
p = filedialog.askopenfilenames(parent=r, title=sys.argv[1], filetypes=[
    ("Video / audio", "*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.wmv *.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
    ("*", "*.*")])
print(json.dumps(list(p)))
"""


# ------------------------------------------------------------------- API ---
@app.get("/api/ping")
def ping():
    return {"app": "teksting"}


LAST_SEEN = [time.time()]


@app.get("/api/state")
def get_state():
    LAST_SEEN[0] = time.time()
    return {**state(), "app_mode": APP_MODE}


@app.post("/api/pick")
def pick(title: str = Body("Velg videoer", embed=True)):
    try:
        out = subprocess.run([sys.executable, "-c", PICKER, title], capture_output=True, text=True, timeout=600)
        paths = json.loads(out.stdout.strip() or "[]")
    except Exception as e:
        raise HTTPException(500, f"Could not open the file picker: {e}")
    return add_jobs({"paths": paths})


@app.post("/api/jobs")
def add_jobs(body: dict = Body(...)):
    s = store.settings()
    added = []
    for p in body.get("paths", []):
        if Path(p).is_file():
            added.append(store.create(str(Path(p).resolve()), body.get("model") or s["model"],
                                      body.get("language") or s["language"], media_duration(p)))
    worker.wake.set()
    return {"added": added, **state()}


@app.post("/api/jobs/{pid}/retry")
def retry(pid: str):
    m = project(pid)
    store.update(pid, status="queued", progress=0.0, message="", eta=None, error_kind=None,
                 model=store.settings()["model"] if m["status"] != "done" else m["model"])
    worker.wake.set()
    return state()


@app.post("/api/jobs/{pid}/cancel")
def cancel(pid: str):
    m = project(pid)
    if worker.current == pid:
        worker.stop_job.set()
    elif m["status"] == "queued":
        store.update(pid, status="cancelled")
    return state()


@app.delete("/api/jobs/{pid}")
def remove(pid: str):
    if worker.current == pid:
        worker.stop_job.set()
        for _ in range(50):
            if worker.current != pid:
                break
            time.sleep(0.1)
    learner.forget_project(pid)
    store.delete(pid)
    return state()


@app.post("/api/queue")
def queue(paused: bool = Body(..., embed=True)):
    worker.paused = paused
    worker.wake.set()
    return state()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    if "glossary" in body:
        store.save_glossary(body.pop("glossary"))
    store.save_settings(body)
    return state()


@app.post("/api/glossary")
def add_glossary(word: str = Body(..., embed=True)):
    store.add_to_glossary(word.strip())
    return {"glossary": store.glossary()}


@app.post("/api/models/{name}/download")
def download_model(name: str):
    if name not in MODELS:
        raise HTTPException(404)
    if worker.current or name in worker.downloading:
        raise HTTPException(409, "busy")

    def go():
        worker.downloading[name] = 0.0
        try:
            engine.download(name, lambda p: worker.downloading.__setitem__(name, p), threading.Event())
        except Exception:
            traceback.print_exc()
        finally:
            worker.downloading.pop(name, None)

    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


@app.get("/api/downloads")
def downloads():
    return worker.downloading


@app.delete("/api/models/{name}")
def delete_model(name: str):
    if name not in MODELS:
        raise HTTPException(404)
    engine.delete(name)
    return state()


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    m = project(pid)
    s = store.settings()
    return {"meta": m, "cues": store.cues(pid), "rules": s["rules"], "style": s["style"],
            "pause_on_type": s.get("pause_on_type", True), "disagree": store.load_json(pid, "disagree.json", []),
            "rescued": store.load_json(pid, "rescued.json", []),
            "autofix": store.load_json(pid, "autofix.json", []),
            "peak_rate": PEAK_RATE}


@app.put("/api/projects/{pid}/cues")
def put_cues(pid: str, cues: list = Body(..., embed=True)):
    project(pid)
    clean = [{"start": round(float(c["start"]), 3), "end": round(float(c["end"]), 3), "text": str(c["text"]),
              "checked": bool(c.get("checked")), **({"pos": "top"} if c.get("pos") == "top" else {})} for c in cues]
    store.save_cues(pid, clean)
    schedule_stats(pid)
    return {"ok": True, "saved": time.time()}


_stat_timers = {}


def schedule_stats(pid, delay=4.0):
    """Recompute edit rate + learned corrections a few seconds after the last save."""
    old = _stat_timers.pop(pid, None)
    if old:
        old.cancel()
    t = threading.Timer(delay, compute_stats, args=(pid,))
    t.daemon = True
    _stat_timers[pid] = t
    t.start()


def compute_stats(pid):
    try:
        m = store.get(pid)
        orig = store.load_json(pid, "cues_orig.json", None)
        if orig is None:
            s = store.settings()
            orig = segment.make_cues(store.words(pid), segment.params(s["rules"]))
            store.save_json(pid, "cues_orig.json", orig)
        rate = learner.update_project(pid, orig, store.cues(pid))
        if m.get("edit_rate") != rate:
            store.update(pid, edit_rate=rate)
    except KeyError:
        pass
    except Exception:
        traceback.print_exc()


@app.post("/api/projects/{pid}/worktime")
def worktime(pid: str, seconds: float = Body(..., embed=True)):
    project(pid)
    with store.lock:
        m = store.get(pid)
        store.update(pid, work_seconds=round(float(m.get("work_seconds") or 0) + max(0.0, min(float(seconds), 120.0)), 1))
    return {"ok": True}


@app.get("/api/learned")
def learned():
    return {"rules": learner.rules(), "min_videos": 2}


@app.put("/api/learned")
def set_learned(key: str = Body(...), state: str = Body(...)):
    if state not in ("auto", "on", "off", "delete"):
        raise HTTPException(400)
    learner.set_state(key, state)
    return {"rules": learner.rules()}


@app.post("/api/projects/{pid}/resegment")
def resegment(pid: str):
    project(pid)
    cues, fixes = finish_cues(pid, store.words(pid), store.settings())
    store.update(pid, edited=False, autofixed=len(fixes))
    schedule_stats(pid)
    return {"cues": cues, "autofix": fixes}


@app.post("/api/spell")
def spell(texts: list = Body(...), language: str = Body("no")):
    allow = allow_set()
    return {"unknown": [speller.unknown(t, language, allow) for t in texts]}


@app.post("/api/wrap")
def wrap(text: str = Body(..., embed=True)):
    return {"text": segment.wrap_text(text, segment.params(store.settings()["rules"]))}


@app.get("/api/projects/{pid}/srt")
def srt(pid: str):
    m = project(pid)
    name = Path(m["name"]).stem + ".srt"
    return Response(segment.to_srt(store.cues(pid)), media_type="application/x-subrip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})


@app.post("/api/projects/{pid}/listen")
def listen(pid: str, start: float = Body(...), end: float = Body(...)):
    m = project(pid)
    name = m["model"] if engine.is_ready(m["model"]) else next((n for n in ("large", "medium", "small") if engine.is_ready(n)), None)
    if not name:
        raise HTTPException(409, "No speech model downloaded")
    s = store.settings()
    prompt = hint_text(s)
    try:
        text = engine.listen(name, m["path"], float(start), float(max(end, start + 0.3)), m["language"], prompt)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)[:200])
    return {"text": segment.wrap_text(text, segment.params(s["rules"])) if text else ""}


@app.get("/api/projects/{pid}/peaks")
def peaks(pid: str):
    project(pid)
    try:
        return Response(peaks_for(pid).read_bytes(), media_type="application/octet-stream")
    except Exception:
        return Response(b"", media_type="application/octet-stream")


@app.post("/api/projects/{pid}/burn")
def burn_video(pid: str):
    m = project(pid)
    if m["status"] != "done":
        raise HTTPException(409, "not transcribed yet")
    if not Path(m["path"]).exists():
        raise HTTPException(404, "Video file has moved or been deleted")
    store.update(pid, export={"status": "queued", "progress": 0.0})
    exporter.wake.set()
    return state()


@app.post("/api/projects/{pid}/burn/cancel")
def burn_cancel(pid: str):
    m = project(pid)
    if exporter.current == pid:
        exporter.stop.set()
    elif (m.get("export") or {}).get("status") == "queued":
        store.update(pid, export={"status": "cancelled"})
    return state()


@app.post("/api/export-all")
def export_all(kind: str = Body(..., embed=True)):
    done = [m for m in store.all() if m["status"] == "done" and Path(m["path"]).exists()]
    saved = []
    for m in done:
        if kind == "srt":
            saved.append(save_srt(m["id"])["path"])
        elif kind == "burn" and (m.get("export") or {}).get("status") not in ("queued", "running"):
            store.update(m["id"], export={"status": "queued", "progress": 0.0})
            saved.append(m["path"])
    exporter.wake.set()
    return {"count": len(saved), "paths": saved, **state()}


@app.post("/api/projects/{pid}/save-srt")
def save_srt(pid: str):
    m = project(pid)
    target = Path(m["path"]).with_suffix(".srt")
    backup = None
    if target.exists():
        backup = target.with_suffix(".srt.bak")
        target.replace(backup)
    target.write_text(segment.to_srt(store.cues(pid)), encoding="utf-8")
    return {"path": str(target), "backup": str(backup) if backup else None}


@app.post("/api/open-folder")
def open_folder(path: str = Body(..., embed=True)):
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if os.name == "nt":
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(folder))  # noqa
    return {"ok": True}


@app.get("/api/projects/{pid}/media")
def media(pid: str, request: Request):
    m = project(pid)
    path = Path(m["path"])
    if not path.exists():
        raise HTTPException(404, "Video file has moved or been deleted")
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rng = request.headers.get("range")
    start, end = 0, size - 1
    if rng and rng.startswith("bytes="):
        a, _, b = rng[6:].split(",")[0].partition("-")
        if a:
            start = int(a)
            end = int(b) if b else size - 1
        elif b:
            start = max(0, size - int(b))
        end = min(end, size - 1)
        if start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=206 if rng else 200, media_type=ctype, headers=headers)


@app.post("/api/quit")
def quit_app():
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"ok": True}


WIN = {"w": None, "hidden": False}  # Teksting's own window (app mode), see run_window


@app.post("/api/show")
def show_window():
    """A second start (shortcut clicked again) brings the existing window forward instead of opening another."""
    w = WIN["w"]
    if not w:
        return {"ok": False}
    WIN["hidden"] = False
    w.show()
    try:
        if str(w.native.WindowState) == "Minimized":
            w.restore()
    except Exception:
        pass
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


# ------------------------------------------------------------------ main ---
def port_in_use():
    """Fast check: on Windows, connecting to a port nobody listens on takes ~2 s before it fails,
    while a local server answers at once - so give up after 0.3 s."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def already_running():
    if not port_in_use():
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "teksting"
    except Exception:
        return False


APP_MODE = "--app" in sys.argv or os.environ.get("TEKSTING_APP") == "1"


def find_app_browser():
    """Edge (on every Windows 10/11) or Chrome, for a window without address bar and tabs."""
    cands = []
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                      Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    return next((str(p) for p in cands if p.exists()), None)


def open_ui(url):
    exe = find_app_browser() if os.name == "nt" else None
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"])
            return
        except OSError:
            pass
    webbrowser.open(url)


def wait_until_ready():
    """Wait for the server to answer, so the window never shows a 'site not available' page."""
    for _ in range(150):  # up to 30 s on a slow PC
        if already_running():
            return
        time.sleep(0.2)


def warm_speller():
    """Loading the dictionary takes ~10 s of pure Python and slows everything else down,
    so it waits until the server answers and the window has had time to open."""
    wait_until_ready()
    time.sleep(6 if APP_MODE else 0)
    speller.warm(store.settings()["language"])


def open_when_ready(url):
    wait_until_ready()
    open_ui(url)


APP_ID = "Teksting.Teksting"  # same ID on the shortcuts (lag-snarveier.ps1) -> one taskbar button, pinnable


def run_window(url, owner):
    """Teksting's own window (WebView2 via pywebview): own taskbar button and icon, no browser involved.
    Blocks until the window is closed. Returns False if no window could be made (then use a browser)."""
    try:
        import ctypes
        import webview
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        print("Own window not available, using a browser:", e)
        return False
    webview.settings["ALLOW_DOWNLOADS"] = True  # "Last ned SRT" opens a save dialog
    if os.environ.get("TEKSTING_DEBUG_PORT"):  # dev: inspect the window with Chrome DevTools
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(os.environ["TEKSTING_DEBUG_PORT"])
    w = webview.create_window("Teksting", url, width=1440, height=900, min_size=(400, 500))

    def on_closing():
        # Closing while a video is transcribing hides the window; the job finishes, then the app quits.
        if owner and busy():
            WIN["hidden"] = True
            threading.Timer(0.05, w.hide).start()
            return False

    w.events.closing += on_closing
    # Teksting.vbs starts pythonw hidden; Windows applies that to our first window, so show it again.
    def on_shown():
        w.show()
        print(f"  window shown after {time.time() - T0:.1f} s")

    w.events.shown += on_shown
    WIN["w"] = w
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA / "webview"),
                      icon=str(APP_DIR / "static" / "teksting.ico"))
    except Exception as e:
        WIN["w"] = None
        print("Own window failed, using a browser:", e)
        traceback.print_exc()
        return False
    return True


def busy():
    return bool(worker.current or exporter.current or any(
        (m["status"] == "queued" and not worker.paused) or (m.get("export") or {}).get("status") in ("queued", "running")
        for m in store.all()))


def watchdog():
    """In app mode there is no console: quit when the window has been closed and nothing is running."""
    limit = float(os.environ.get("TEKSTING_IDLE_EXIT", "90"))
    while True:
        time.sleep(min(20.0, limit / 2))
        if WIN["w"]:  # own window: quit once it is hidden and the last job is done
            if WIN["hidden"] and not busy():
                os._exit(0)
        elif time.time() - LAST_SEEN[0] > limit and not busy():
            os._exit(0)


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if APP_MODE and (sys.stdout is None or "pythonw" in Path(sys.executable).name.lower()):
        log = open(DATA / "teksting.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print("\n---", time.strftime("%Y-%m-%d %H:%M:%S"), "starting")
    if already_running():
        if APP_MODE:
            try:
                req = urllib.request.Request(f"{url}api/show", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=3) as r:
                    if json.load(r).get("ok"):
                        return
            except Exception:
                pass
            if run_window(url, owner=False):
                return
        open_ui(url)
        return
    if port_in_use():
        print(f"Port {PORT} is used by another program. Set TEKSTING_PORT to another number.")
        return
    for m in store.all():  # jobs that were running when the app closed start again
        if m["status"] in ("downloading", "loading", "transcribing", "checking", "finishing"):
            store.update(m["id"], status="queued", progress=0.0, eta=None)
        if (m.get("export") or {}).get("status") == "running":
            store.update(m["id"], export={"status": "queued"})
    worker.start()
    exporter.start()
    threading.Thread(target=warm_speller, daemon=True).start()
    print("\n  Teksting is running:", url)
    import uvicorn
    if APP_MODE and os.name == "nt" and os.environ.get("TEKSTING_NO_BROWSER") != "1":
        # The window must own the main thread, so the server runs beside it.
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        threading.Thread(target=watchdog, daemon=True).start()
        wait_until_ready()
        print(f"  server ready after {time.time() - T0:.1f} s")
        if run_window(url, owner=True):
            os._exit(0)  # window closed and nothing running (closing while busy only hides it)
        open_ui(url)  # no WebView2: fall back to a browser window, quit via the watchdog as before
        while True:
            time.sleep(3600)
    if os.environ.get("TEKSTING_NO_BROWSER") != "1":
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    if APP_MODE:
        threading.Thread(target=watchdog, daemon=True).start()
    print("  Keep this window open while you work. Close it to quit.\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
