"""Shared test setup.

The server module reads its settings from the environment when it is imported, so the environment is set
here, before any test imports it: fake transcription (no speech models needed), a throwaway data folder,
and no window or browser.
"""
import os
import sys
import tempfile
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_DATA = tempfile.mkdtemp(prefix="teksting-test-")
os.environ.update(TEKSTING_FAKE="1", TEKSTING_DATA=_DATA, TEKSTING_NO_BROWSER="1", TEKSTING_NO_SHORTCUTS="1")


@pytest.fixture(scope="session")
def server():
    import server as srv
    srv.speller.unknown = lambda *a, **k: []  # the real dictionary is a 10 s download + load; not under test here
    return srv


@pytest.fixture(scope="session")
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def _clean_projects(request):
    """Every API test starts with no videos and default settings."""
    if "server" not in request.fixturenames and "client" not in request.fixturenames:
        yield
        return
    srv = request.getfixturevalue("server")
    yield
    for m in srv.store.all():
        srv.store.delete(m["id"])
    (Path(_DATA) / "settings.json").unlink(missing_ok=True)


@pytest.fixture
def no_sleep(server, monkeypatch):
    """Fake transcription waits ~13 s per pass to look realistic; tests don't need to wait."""
    import time as real_time
    from tekst import engine
    monkeypatch.setattr(engine, "time", types.SimpleNamespace(**{**vars(real_time), "sleep": lambda s: None}))


@pytest.fixture
def make_audio(tmp_path):
    """make_audio("navn.wav", seconds) -> path to a silent 16 kHz mono WAV file."""
    def make(name="video.wav", seconds=20.0, folder=None):
        d = Path(folder) if folder else tmp_path
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * int(16000 * seconds))
        return p
    return make
