"""Segmentation (word timings -> subtitles) and the SRT format."""
import re

import pytest

from tekst import segment
from tekst.segment import make_cues, params, to_srt, ts, wrap_text

TS = r"\d{2}:\d{2}:\d{2},\d{3}"
BLOCK = re.compile(rf"^(\d+)\n({TS}) --> ({TS})\n((?:[^\n]+\n?)+)$")


def words_from(text, start=0.5, per_word=0.32, pause_after=None):
    """Turn a sentence into fake model words, evenly timed; pause_after = {index: extra seconds}."""
    out, t = [], start
    for k, w in enumerate(text.split()):
        out.append({"w": " " + w, "s": round(t, 3), "e": round(t + per_word - 0.05, 3), "p": 0.9})
        t += per_word + (pause_after or {}).get(k, 0.0)
    return out


def parse_srt(text):
    """Strict reader: numbered 1, 2, 3 ..., 'HH:MM:SS,mmm --> HH:MM:SS,mmm', 1+ text lines, one blank line between."""
    blocks = text.strip("\n").split("\n\n") if text.strip() else []
    out = []
    for k, b in enumerate(blocks, 1):
        m = BLOCK.match(b)
        assert m, f"bad block {k}: {b!r}"
        assert int(m.group(1)) == k
        out.append((m.group(2), m.group(3), m.group(4).rstrip("\n")))
    return out


# ----------------------------------------------------------------- timestamps
@pytest.mark.parametrize("sec, expected", [
    (0, "00:00:00,000"),
    (1.0004, "00:00:01,000"),
    (4.09, "00:00:04,090"),          # float noise must not give 4,089
    (59.9996, "00:01:00,000"),        # rounding carries into the minutes
    (3599.9999, "01:00:00,000"),
    (3723.456, "01:02:03,456"),
    (-0.5, "00:00:00,000"),
])
def test_timestamps(sec, expected):
    assert ts(sec) == expected


# ------------------------------------------------------------------------ SRT
def test_srt_is_strict_and_clean():
    cues = [
        {"start": 5.0, "end": 7.5, "text": "Kommer etter"},
        {"start": 0.6, "end": 4.09, "text": "Hei og velkommen\npresentasjonen."},
        {"start": 8.0, "end": 9.0, "text": "   "},                                   # empty: skipped
        {"start": 9.5, "end": 11.0, "text": "  Mellomrom  \n\n  og tom linje  "},
        {"start": 12.0, "end": 14.0, "text": "Æøå «sitat» – … “x” < & >"},
        {"start": 14.5, "end": 16.0, "text": "Tall alene:\n2019"},
        {"start": 16.5, "end": 18.0, "text": "Pil --> her"},
        {"start": 18.5, "end": 20.0, "text": "Windows\r\nlinjeskift"},
    ]
    blocks = parse_srt(to_srt(cues))
    assert [b[2] for b in blocks] == [
        "Hei og velkommen\npresentasjonen.", "Kommer etter", "Mellomrom\nog tom linje",
        "Æøå «sitat» – … “x” < & >", "Tall alene:\n2019", "Pil --> her", "Windows\nlinjeskift",
    ]
    assert blocks[0][:2] == ("00:00:00,600", "00:00:04,090")
    assert "\n\n\n" not in to_srt(cues)


def test_empty_project_gives_empty_file():
    assert to_srt([]) == ""


# ---------------------------------------------------------------- segmenting
LONG = ("I dag skal vi snakke om hvordan Telenor Norge har jobbet med bærekraft de siste fem årene, "
        "og hva vi har lært underveis. For det første er det viktig å forstå at utslippene våre har gått "
        "ned med 23 prosent siden 2019. Det er et resultat av mange små og store endringer i hele organisasjonen.")


def test_cues_follow_the_rules():
    p = params()
    cues = make_cues(words_from(LONG, pause_after={16: 0.8, 35: 0.8}), p)
    assert len(cues) > 3
    for k, c in enumerate(cues):
        lines = c["text"].split("\n")
        assert len(lines) <= 2
        assert all(len(l) <= p.max_chars for l in lines), c["text"]
        assert c["end"] - c["start"] <= p.max_dur + 1e-6
        assert c["end"] > c["start"]
        if k:
            assert c["start"] - cues[k - 1]["end"] >= p.gap - 1e-6, "cues must not overlap"
    # nothing is lost or reordered
    assert " ".join(c["text"].replace("\n", " ") for c in cues) == LONG


def test_number_stays_with_its_unit():
    cues = make_cues(words_from(LONG), params())
    assert any("23 prosent" in c["text"].replace("\n", " ") for c in cues)


def test_fillers_are_removed_and_next_word_capitalised():
    cues = make_cues(words_from("Hei. eh vi starter nå. ehm og så videre"), params())
    text = " ".join(c["text"] for c in cues)
    assert "eh" not in text.split() and "ehm" not in text.split()
    assert "Vi starter" in text.replace("\n", " ")


def test_collapse_repeats_only_when_switched_on():
    w = words_from("jeg jeg tror det")
    assert "jeg jeg" in make_cues(w, params())[0]["text"]
    assert make_cues(words_from("jeg jeg tror det"), params(collapse_repeats=True))[0]["text"].startswith("jeg tror")


def test_comma_glued_to_next_word_is_given_back():
    w = words_from("Hei trøndere velkommen")
    w[1]["w"] = ",trøndere"
    assert make_cues(w, params())[0]["text"].startswith("Hei, trøndere")


def test_wrap_text_balances_two_lines():
    text = "Det er et resultat av mange små og store endringer i hele organisasjonen,"
    lines = wrap_text(text, params()).split("\n")
    assert len(lines) == 2
    assert all(len(l) <= 42 for l in lines)
    assert abs(len(lines[0]) - len(lines[1])) < 15
    assert wrap_text("Kort tekst", params()) == "Kort tekst"
    assert wrap_text("   ", params()) == ""


def test_params_ignores_unknown_keys():
    p = params({"max_chars": 37, "nonsense": 1})
    assert p.max_chars == 37 and isinstance(p, segment.P)
