"""Learn from the user's corrections.

For each video we compare the automatic subtitles (cues_orig) with what the user ended up with.
Short word replacements ("Telenord" -> "Telenor") become rules. A rule fixes new videos
automatically once it has been seen in 2+ videos, or right away if the user switches it on.
The same comparison gives the edit rate: the share of words the user had to change.
"""
import difflib
import json
import re
import threading
from pathlib import Path

TOKEN = re.compile(r"[^\W_][\w'’-]*", re.U)
# Small words we never turn into global rules ("og" -> "i" is context, not a recurring mishearing)
STOP = set("""og i er det en ei et som på til av for med at de den har var vi jeg du han hun ikke å så om
men eller kan skal vil fra der her nå da også bare vel jo ja nei the and""".split())
AUTO_MIN_VIDEOS = 2


def _tokens(text):
    return TOKEN.findall(text)


def compare(orig_cues, cues):
    """Return (pairs, edit_rate). pairs = {(wrong, right)} of short word replacements."""
    A = _tokens(" ".join(c["text"] for c in orig_cues))
    B = _tokens(" ".join(c["text"] for c in cues))
    sm = difflib.SequenceMatcher(None, [a.lower() for a in A], [b.lower() for b in B], autojunk=False)
    changed, pairs = 0, set()
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
        if op != "replace" or not (1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 3):
            continue
        a, b = A[i1:i2], B[j1:j2]
        sa, sb = " ".join(a), " ".join(b)
        if sa.lower() == sb.lower() or len(sa) < 3:
            continue
        if all(t.lower() in STOP for t in a) and all(t.lower() in STOP for t in b):
            continue
        pairs.add((sa, sb))
    rate = changed / max(1, len(A))
    return pairs, round(rate, 4)


def _key(a, b):
    return a.lower() + "→" + b


class Learner:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"rules": {}}

    def _save(self, d):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def update_project(self, pid, orig_cues, cues):
        """Re-learn this video's corrections. Returns the edit rate."""
        pairs, rate = compare(orig_cues, cues)
        with self.lock:
            d = self._load()
            rules = d["rules"]
            for r in rules.values():
                r["videos"] = [v for v in r.get("videos", []) if v != pid]
                r["rejected"] = [v for v in r.get("rejected", []) if v != pid]
            for a, b in pairs:
                rev = rules.get(_key(b, a))
                if rev:  # the user undid an automatic fix: count against that rule
                    rev.setdefault("rejected", []).append(pid)
                    continue
                r = rules.setdefault(_key(a, b), {"a": a, "b": b, "videos": [], "rejected": [], "state": "auto"})
                r["videos"].append(pid)
            d["rules"] = {k: r for k, r in rules.items() if r["videos"] or r["rejected"] or r.get("state") != "auto"}
            self._save(d)
        return rate

    def forget_project(self, pid):
        with self.lock:
            d = self._load()
            for r in d["rules"].values():
                r["videos"] = [v for v in r.get("videos", []) if v != pid]
                r["rejected"] = [v for v in r.get("rejected", []) if v != pid]
            d["rules"] = {k: r for k, r in d["rules"].items() if r["videos"] or r["rejected"] or r.get("state") != "auto"}
            self._save(d)

    @staticmethod
    def is_active(r):
        if r.get("state") == "on":
            return True
        if r.get("state") == "off":
            return False
        return len(r.get("videos", [])) >= AUTO_MIN_VIDEOS and not r.get("rejected")

    def rules(self):
        with self.lock:
            out = []
            for k, r in self._load()["rules"].items():
                out.append({"key": k, "a": r["a"], "b": r["b"], "videos": len(r.get("videos", [])),
                            "rejected": len(r.get("rejected", [])), "state": r.get("state", "auto"),
                            "active": self.is_active(r)})
            return sorted(out, key=lambda r: (-r["videos"], r["a"].lower()))

    def set_state(self, key, state):
        with self.lock:
            d = self._load()
            if key in d["rules"]:
                if state == "delete":
                    del d["rules"][key]
                else:
                    d["rules"][key]["state"] = state
                self._save(d)

    def active(self):
        return [r for r in self.rules() if r["active"]]

    def apply(self, cues, wrap=None, max_chars=42):
        """Apply active rules to cue texts. Returns list of fixes [{"t","a","b"}]."""
        rules = self.active()
        fixes = []
        if not rules:
            return fixes
        compiled = []
        for r in rules:
            pat = r"(?<![\w])" + r"\s+".join(re.escape(p) for p in r["a"].split()) + r"(?![\w])"
            compiled.append((re.compile(pat, re.I | re.U), r["b"]))
        for c in cues:
            text = c["text"]
            for rx, b in compiled:
                def rep(m, b=b):
                    out = b[0].upper() + b[1:] if m.group(0)[:1].isupper() and b[:1].islower() else b
                    if m.group(0) != out:
                        fixes.append({"t": c["start"], "a": m.group(0).replace("\n", " "), "b": out})
                    return out
                text = rx.sub(rep, text)
            if text != c["text"]:
                if wrap and any(len(l) > max_chars for l in text.split("\n")):
                    text = wrap(text)
                c["text"] = text
        return fixes

    def hint_words(self):
        return [r["b"] for r in self.active()]
