"""The file-based store: projects, settings, glossary."""
import pytest

from tekst.store import DEFAULT_SETTINGS, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def test_create_get_update_delete(store):
    m = store.create("C:/videoer/Årsmøte.mp4", "large", "no", 61.5)
    assert m["name"] == "Årsmøte.mp4" and m["status"] == "queued"
    store.update(m["id"], status="done", cues=12)
    assert store.get(m["id"])["status"] == "done"
    assert [x["id"] for x in store.all()] == [m["id"]]
    store.delete(m["id"])
    with pytest.raises(KeyError):
        store.get(m["id"])
    assert store.all() == []


def test_returned_projects_are_copies(store):
    m = store.create("a.mp4", "large", "no", 10)
    got = store.get(m["id"])
    got["status"] = "changed by caller"
    store.all()[0]["name"] = "changed too"
    assert store.get(m["id"])["status"] == "queued"
    assert store.get(m["id"])["name"] == "a.mp4"


def test_survives_a_restart(tmp_path):
    a = Store(tmp_path)
    m = a.create("a.mp4", "large", "no", 10)
    a.save_cues(m["id"], [{"start": 1, "end": 2, "text": "Hei"}])
    b = Store(tmp_path)  # a fresh instance reads everything back from disk
    assert b.get(m["id"])["cues"] == 1 and b.get(m["id"])["edited"] is True
    assert b.cues(m["id"])[0]["text"] == "Hei"


def test_words_round_trip_in_chunks(store):
    m = store.create("a.mp4", "large", "no", 10)
    words = [{"w": f" w{k}", "s": k, "e": k + 0.5, "p": 0.9} for k in range(95)]
    store.save_words(m["id"], words)
    assert store.words(m["id"]) == words


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "missing"])
def test_project_ids_cannot_escape_the_folder(store, bad):
    with pytest.raises(KeyError):
        store.cues(bad)


def test_cleanup_removes_half_deleted_projects_and_burn_leftovers(store):
    keep = store.create("a.mp4", "large", "no", 10)
    (store.projects / keep["id"] / "burn").mkdir()
    (store.projects / "orphan").mkdir()
    (store.projects / "orphan" / "words.jsonl").write_text("x")
    store.cleanup()
    assert not (store.projects / "orphan").exists()
    assert not (store.projects / keep["id"] / "burn").exists()
    assert store.get(keep["id"])


def test_settings_merge_defaults_and_ignore_unknown_keys(store):
    s = store.settings()
    assert s["model"] == DEFAULT_SETTINGS["model"] and s["burn_tools"] is False
    store.save_settings({"burn_tools": True, "rules": {"max_chars": 37}, "evil": 1})
    s = store.settings()
    assert s["burn_tools"] is True
    assert s["rules"]["max_chars"] == 37 and s["rules"]["max_cps"] == DEFAULT_SETTINGS["rules"]["max_cps"]
    assert "evil" not in s


def test_glossary_adds_each_word_once(store):
    store.add_to_glossary("Kygo")
    store.add_to_glossary("kygo")
    assert store.glossary_terms() == ["Kygo"]
    assert "kygo" in store.glossary_allow()
