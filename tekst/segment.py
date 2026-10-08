"""Subtitle segmentation: word timings -> Netflix-style cues (Norwegian).

Rules (defaults, adjustable in Settings): max 2 lines of 42 characters, 1-7 s on
screen, max 17 characters per second, at least 0.08 s (2 frames) between cues.
"""
import re
from dataclasses import dataclass, fields

@dataclass
class P:  # all tunables in one place
    max_chars: int = 42
    min_dur: float = 1.0
    max_dur: float = 7.0
    max_cps: float = 17.0
    gap: float = 0.08          # min silence between consecutive cues
    tail: float = 0.30         # how long a cue lingers after the last word
    lead: float = 0.0          # shift cue start earlier (seconds)
    hard_pause: float = 1.2    # never join words across a pause this long
    low_prob: float = 0.0      # >0 flags words below this confidence (off: it was mostly noise)
    gap_warn: float = 3.0      # flag a cue that follows this many seconds of "silence"
    dict_path: str = ""        # folder with nb_NO.aff/.dic (default: ./dict next to this script)
    glossary: str = ""         # names/terms the spell checker should treat as correct
    collapse_repeats: bool = False


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


# ---------------------------------------------------------------- cleaning ---
# "eh", "ehm", "øh", "mm", "mhm", "hmm" ... (real words like "ah" or "æ" are NOT removed)
FILLER_RE = re.compile(
    r"^(?:e{2,}h*m*|eh+m*|ehm+|em+|øh+m*|ø{2,}h*|æh+m*|æ{2,}h*|m{2,}h*|mh+m*|hm+|uh+m*|um+)$",
    re.I,
)
SENT_END = re.compile(r"[.!?…][\"»”)]*$")


def core(w):
    t = w.text if isinstance(w, Word) else w
    return re.sub(r"\W+", "", t.lower())


def clean_words(words, p):
    out, cap_next = [], False
    for w in words:
        raw = w.text
        w.text = raw.strip()
        if not w.text:
            continue
        # Whisper sometimes glues a comma/period to the NEXT word (",trøndere,") - give it back
        m = re.match(r"^([,.;:!?…]+)(\S.*)$", w.text)
        if m and out and not raw[:1].isspace():
            out[-1].text += m.group(1)
            w.text = m.group(2)
        c = core(w)
        if FILLER_RE.match(c):
            if not out or SENT_END.search(out[-1].text):
                cap_next = True  # filler opened a sentence -> next word gets the capital
            continue
        if p.collapse_repeats and out and c == core(out[-1]) and len(c) <= 4:
            out[-1].end = max(out[-1].end, w.end)  # "jeg jeg" -> "jeg"
            continue
        if cap_next:
            w.text = w.text[0].upper() + w.text[1:]
            cap_next = False
        if out:  # keep timestamps monotonic (Whisper words can overlap slightly)
            w.start = max(w.start, out[-1].start)
        w.end = max(w.end, w.start)
        out.append(w)
    return out


# ------------------------------------------------------- break preferences ---
# Words a cue/line should NOT end on (they belong with what follows).
NO_END_STRONG = set("""en ei et den det de i på av til fra med for om ved over under etter før mot hos
    å og eller men at som hvis når hvor fordi mens min mitt mine din ditt dine sin sitt sine vår vårt våre
    deres hans hennes dens dets denne dette disse noen noe alle""".split())
NO_END_MED = set("""jeg du vi dere han hun man ikke også så bare helt veldig litt
    ny nye nytt stor store stort liten lite små mange flere hele hver hvert første andre neste siste samme
    egen egne god gode godt viktig viktige to tre fire fem seks sju syv åtte ni ti hundre tusen""".split())
# Good words to START a new cue/line with (new clause).
GOOD_START = set("og men eller at som fordi hvis når mens så".split())
UNITS = set("prosent kroner kr millioner millioner milliarder tusen år måneder uker dager timer minutter".split())


def break_cost(a, b):
    """Cost of ending a cue/line after word a and starting the next with b (lower = better)."""
    ca, cb = core(a), core(b)
    cost = 0.0
    if SENT_END.search(a.text):
        cost -= 8
    elif re.search(r"[,;:–—-]$", a.text):
        cost -= 4
    pause = b.start - a.end
    if pause >= 0.6:
        cost -= 6
    elif pause >= 0.3:
        cost -= 3
    if ca in NO_END_STRONG:
        cost += 10
    elif ca in NO_END_MED:
        cost += 5
    if cb in GOOD_START:
        cost -= 3
    if a.text[:1].isupper() and b.text[:1].isupper() and not re.search(r"[.!?,:;]$", a.text):
        cost += 8  # don't split names ("Våg | Bakeri")
    if ca.isdigit() and not SENT_END.search(a.text):
        cost += 8 if cb in UNITS else 4  # keep numbers with what they count
    return cost


# -------------------------------------------------------------- cue search ---
def split_lines(sub, p):
    text = " ".join(w.text for w in sub)
    if len(text) <= p.max_chars:
        return [text], 0.0
    best = None
    for k in range(1, len(sub)):
        l1 = " ".join(w.text for w in sub[:k])
        l2 = " ".join(w.text for w in sub[k:])
        if len(l1) > p.max_chars or len(l2) > p.max_chars:
            continue
        c = break_cost(sub[k - 1], sub[k]) + abs(len(l1) - len(l2)) * 0.15 + (1.0 if len(l1) > len(l2) else 0.0)
        if best is None or c < best[1]:
            best = ([l1, l2], c)
    return best  # None if it cannot fit in two lines


def cue_cost(words, i, j, p):
    sub = words[i:j]
    if len(sub) == 1:  # always allowed, whatever its length/duration
        return 6.0 + 12, [sub[0].text]
    span = sub[-1].end - sub[0].start
    if span > p.max_dur:
        return None
    if any(b.start - a.end > p.hard_pause for a, b in zip(sub, sub[1:])):
        return None
    sp = split_lines(sub, p)
    if sp is None:
        return None
    lines, lc = sp
    chars = sum(len(l) for l in lines)
    cps = chars / max(span + p.tail, p.min_dur)
    cost = 12 + 0.5 * lc + (3 if len(lines) == 2 else 0)
    if cps > p.max_cps:
        cost += (cps - p.max_cps) * 6
    if chars < 8:
        cost += 6  # avoid orphan cues
    if span > 5.0:
        cost += (span - 5.0) * 3  # prefer splitting at a natural pause over a very long cue
    if j < len(words):
        cost += break_cost(words[j - 1], words[j])
    return cost, lines


def build_cues(words, p):
    n = len(words)
    INF = 1e18
    pre = [0]
    for w in words:
        pre.append(pre[-1] + len(w.text))
    best, prev = [INF] * (n + 1), [-1] * (n + 1)
    lines_at = [None] * (n + 1)
    best[0] = 0.0
    for i in range(n):
        if best[i] >= INF:
            continue
        for j in range(i + 1, n + 1):
            if pre[j] - pre[i] + (j - i - 1) > 2 * p.max_chars and j - i > 1:
                break
            r = cue_cost(words, i, j, p)
            if r is None:
                continue
            c, lines = r
            if best[i] + c < best[j]:
                best[j], prev[j], lines_at[j] = best[i] + c, i, lines
    cues, j = [], n
    while j > 0:
        i = prev[j]
        cues.append({"i": i, "j": j, "lines": lines_at[j]})
        j = i
    cues.reverse()
    return cues



def time_cues(cues, words, p):
    for c in cues:
        ws = words[c["i"]:c["j"]]
        c["start"] = max(0.0, ws[0].start - p.lead)
        c["end"] = ws[-1].end + p.tail
        c["chars"] = sum(len(l) for l in c["lines"])
    for k, c in enumerate(cues):
        nxt = cues[k + 1]["start"] - p.gap if k + 1 < len(cues) else float("inf")
        limit = min(nxt, c["start"] + p.max_dur)
        end = min(c["end"], limit)
        if end - c["start"] < p.min_dur:
            end = min(c["start"] + p.min_dur, limit)
        need = c["chars"] / p.max_cps  # extend into silence if reading speed is too high
        if end - c["start"] < need:
            end = min(c["start"] + need, limit)
        c["end"] = max(end, c["start"] + 0.2)
    return cues


# --------------------------------------------------------------------- API ---
def params(rules=None, **extra):
    """Build P from a settings dict, ignoring unknown keys."""
    names = {f.name for f in fields(P)}
    kw = {k: v for k, v in {**(rules or {}), **extra}.items() if k in names}
    return P(**kw)


def make_cues(raw_words, p):
    """raw_words: [{"w","s","e","p"}] -> [{"start","end","text"}] (text lines joined by \\n)."""
    words = clean_words([Word(w["w"], w["s"], w["e"], w.get("p", 1.0)) for w in raw_words], p)
    if not words:
        return []
    cues = time_cues(build_cues(words, p), words, p)
    return [{"start": round(c["start"], 3), "end": round(c["end"], 3), "text": "\n".join(c["lines"])}
            for c in cues]


def wrap_text(text, p):
    """Re-break one cue's text into at most two balanced lines (same rules as the segmenter)."""
    toks = text.split()
    if not toks:
        return ""
    ws = [Word(t, 0.0, 0.0) for t in toks]
    sp = split_lines(ws, p)
    return "\n".join(sp[0]) if sp else " ".join(toks)


def ts(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(cues):
    out = []
    n = 0
    for c in sorted(cues, key=lambda c: c["start"]):
        text = "\n".join(l.strip() for l in c["text"].splitlines() if l.strip())
        if not text:
            continue
        n += 1
        out.append(f"{n}\n{ts(c['start'])} --> {ts(c['end'])}\n{text}\n")
    return "\n".join(out)
