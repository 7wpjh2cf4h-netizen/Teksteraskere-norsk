"""Projects (one per video), settings and glossary, stored as plain files under data/."""
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

DEFAULT_SETTINGS = {
    "model": "large",
    "language": "no",              # "no" = Bokmål, "nn" = Nynorsk
    "glossary_hint": False,        # also give the glossary to the model as a hint (experimental)
    "collapse_repeats": False,
    "ui_lang": "",                 # "" = follow the browser
    "hotwords": True,              # give glossary + learned corrections to the model as hints
    "double_check": True,          # also run the fast model and flag where the two disagree
    "pause_on_type": True,         # pause the video when you start typing in the editor
    "rules": {"max_chars": 42, "max_cps": 17.0, "min_dur": 1.0, "max_dur": 7.0},
    "style": {},                   # burned-in subtitle look (see burn.DEFAULT_STYLE)
}


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    # ---- settings & glossary
    def settings(self):
        s = _read_json(self.root / "settings.json", {})
        merged = {**DEFAULT_SETTINGS, **s}
        merged["rules"] = {**DEFAULT_SETTINGS["rules"], **s.get("rules", {})}
        from .burn import DEFAULT_STYLE
        merged["style"] = {**DEFAULT_STYLE, **s.get("style", {})}
        return merged

    def save_settings(self, new):
        with self.lock:
            cur = self.settings()
            for k in DEFAULT_SETTINGS:
                if k in new:
                    cur[k] = new[k]
            _write_json(self.root / "settings.json", cur)
            return cur

    def glossary(self):
        f = self.root / "glossary.txt"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def save_glossary(self, text):
        (self.root / "glossary.txt").write_text(text, encoding="utf-8")

    def glossary_terms(self):
        return [t.strip() for t in self.glossary().splitlines() if t.strip()]

    def glossary_allow(self):
        return frozenset(w.lower() for t in self.glossary_terms() for w in t.split())

    def add_to_glossary(self, word):
        with self.lock:
            terms = self.glossary_terms()
            if word.lower() not in {t.lower() for t in terms}:
                terms.append(word)
                self.save_glossary("\n".join(terms) + "\n")

    # ---- projects
    def _dir(self, pid):
        d = self.projects / pid
        if not d.is_dir() or "/" in pid or "\\" in pid or ".." in pid:
            raise KeyError(pid)
        return d

    def create(self, path, model, language, duration):
        pid = uuid.uuid4().hex[:10]
        d = self.projects / pid
        d.mkdir()
        meta = {"id": pid, "name": Path(path).name, "path": str(path), "created": time.time(),
                "status": "queued", "progress": 0.0, "message": "", "duration": duration,
                "model": model, "language": language, "eta": None, "edited": False, "cues": 0}
        _write_json(d / "meta.json", meta)
        return meta

    def all(self):
        out = []
        for d in self.projects.iterdir():
            m = _read_json(d / "meta.json", None)
            if m:
                out.append(m)
        return sorted(out, key=lambda m: m["created"])

    def get(self, pid):
        m = _read_json(self._dir(pid) / "meta.json", None)
        if m is None:
            raise KeyError(pid)
        return m

    def update(self, pid, **kw):
        with self.lock:
            m = self.get(pid)
            m.update(kw)
            _write_json(self._dir(pid) / "meta.json", m)
            return m

    def delete(self, pid):
        with self.lock:
            shutil.rmtree(self._dir(pid), ignore_errors=True)

    def words_path(self, pid):
        return self._dir(pid) / "words.jsonl"

    def words(self, pid):
        out = []
        f = self.words_path(pid)
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out += json.loads(line)["words"]
        return out

    def save_words(self, pid, words):
        f = self.words_path(pid)
        tmp = f.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for k in range(0, len(words), 40):
                fh.write(json.dumps({"words": words[k:k + 40]}, ensure_ascii=False) + "\n")
        tmp.replace(f)

    def save_json(self, pid, name, data):
        _write_json(self._dir(pid) / name, data)

    def load_json(self, pid, name, default=None):
        return _read_json(self._dir(pid) / name, default)

    def cues(self, pid):
        return _read_json(self._dir(pid) / "cues.json", [])

    def save_cues(self, pid, cues, edited=True):
        with self.lock:
            _write_json(self._dir(pid) / "cues.json", cues)
            self.update(pid, cues=len(cues), **({"edited": True} if edited else {}))
