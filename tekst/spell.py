"""Norwegian spell check (LibreOffice hunspell dictionaries via spylls). Downloads on first use."""
import re
import threading
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/no/{}"
DICTS = {"no": "nb_NO", "nn": "nn_NO"}  # Whisper language code -> dictionary
TOKEN = re.compile(r"[^\W\d_](?:[\w'’-]*[^\W_])?")


class Speller:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._dicts = {}
        self._lock = threading.Lock()

    def _load(self, lang):
        name = DICTS.get(lang, "nb_NO")
        with self._lock:
            if name in self._dicts:
                return self._dicts[name]
            for ext in (".aff", ".dic"):
                f = self.dir / (name + ext)
                if not f.exists():
                    tmp = f.with_suffix(f.suffix + ".part")
                    urllib.request.urlretrieve(URL.format(name + ext), tmp)
                    tmp.replace(f)
            from spylls.hunspell import Dictionary
            d = Dictionary.from_files(str(self.dir / name))
            self._dicts[name] = d
            return d

    def warm(self, lang="no"):
        try:
            self._load(lang)
        except Exception as e:  # offline etc. - spell check just stays off
            print("spell check unavailable:", e)

    def unknown(self, text, lang="no", allow=frozenset()):
        """Return the words in `text` that are not in the dictionary or the glossary."""
        try:
            d = self._load(lang)
        except Exception:
            return []

        def known(w):
            return bool(d.lookup(w) or d.lookup(w.lower()) or d.lookup(w.capitalize()))

        out = []
        for w in TOKEN.findall(text):
            if w.lower() in allow or known(w):
                continue
            parts = [x for x in re.split(r"[-'’]", w) if len(x) >= 3]
            if len(parts) > 1 and all(known(x) or x.lower() in allow for x in parts):
                continue
            if w not in out:
                out.append(w)
        return out
