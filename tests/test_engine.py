"""Engine helpers that don't need a speech model."""
import numpy as np

from tekst.engine import PEAK_RATE, Engine, peaks_from_audio, physical_cores


def test_physical_cores_is_clamped():
    assert 4 <= physical_cores() <= 8


def test_peaks_one_value_per_50th_second_and_scaled_to_a_byte():
    sr = 16000
    audio = np.zeros(sr * 2, dtype=np.float32)
    audio[sr:] = 0.5  # silence, then sound
    p = peaks_from_audio(audio, sr)
    assert len(p) == 2 * PEAK_RATE
    assert max(p[:PEAK_RATE]) == 0 and min(p[PEAK_RATE:]) > 200
    assert peaks_from_audio(np.zeros(10, dtype=np.float32), sr) == b""


def test_unload_keeps_what_is_asked_for(tmp_path):
    e = Engine(tmp_path)
    e._loaded.update(large=object(), small=object())
    assert sorted(e.unload(keep={"large"})) == ["small"]
    assert e.is_loaded("large") and not e.is_loaded("small")
    assert e.unload() == ["large"] and e.unload() == []

