"""Saving over a damaged settings file keeps a ``.corrupt-*`` copy of it
(never silently erases what it still held), a healthy file that's only
unreadable for a moment is never moved aside, and what the reader would
reword or drop is kept on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_GARBAGE = b"\xff\xfe\x00garbage"


@pytest.fixture(autouse=True)
def _no_real_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BPP_BYOM_PATH", str(tmp_path / "byom-default.json"))


def test_use_context_history_is_kept_verbatim(tmp_path):
    from bpp.registry.use_context_store import read_record

    path = tmp_path / "use-context.json"
    old = {"use_context": None, "set_at": 5, "set_via": "cli"}
    path.write_text(json.dumps({"use_context": "personal", "history": [old]}))
    assert read_record(path=path).history == (old,)


def test_saving_the_use_context_never_rewords_its_history(tmp_path):
    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    old = {"use_context": None, "set_at": 5}
    path.write_text(json.dumps({"use_context": "personal", "history": [old]}))
    set_use_context(UseContext.RESEARCH, set_via="test", path=path)
    assert json.loads(path.read_text())["history"][0] == old  # not {"use_context": "None", ...}
    assert _corrupt_copies(path) == []


def test_saving_over_a_use_context_file_the_reader_alters_keeps_a_copy(tmp_path):
    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    original = json.dumps({"use_context": "personal", "history": ["not a dict"]})
    path.write_text(original)
    set_use_context(UseContext.RESEARCH, set_via="test", path=path)
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == original


# ── a damaged file is set aside, never silently overwritten ──────────


def _corrupt_copies(path):
    return sorted(p.name for p in path.parent.glob(path.name + ".corrupt-*"))


def test_saving_the_use_context_keeps_a_damaged_file(tmp_path, monkeypatch):
    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    path.write_text("[]")
    set_use_context(UseContext.PERSONAL, set_via="test", path=path)
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == "[]"
    assert json.loads(path.read_text())["use_context"] == "personal"


def test_saving_the_use_context_over_a_good_file_keeps_no_copy(tmp_path):
    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    set_use_context(UseContext.PERSONAL, set_via="test", path=path)
    set_use_context(UseContext.COMMERCIAL, set_via="test", path=path)
    assert _corrupt_copies(path) == []


def test_adding_a_model_keeps_a_damaged_store(tmp_path):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    path.write_bytes(_GARBAGE)
    fp = tmp_path / "m.onnx"
    fp.write_bytes(b"model")
    byom.add_byom_entry(display_name="M", kind="face_embedder", file_path=fp, path=path)
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_bytes() == _GARBAGE


def test_saving_the_library_list_keeps_a_damaged_file(tmp_path, monkeypatch):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    path.write_text('{"libraries": "oops"}')
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    registry.save_registry({"libraries": [], "active": None})
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == '{"libraries": "oops"}'


def test_setting_aside_a_file_that_just_vanished_is_a_no_op(tmp_path, monkeypatch):
    from bpp.utils import json_utils

    path = tmp_path / "use-context.json"
    path.write_text("[]")

    def moved_by_another_save(src, dst):
        raise FileNotFoundError(src)

    monkeypatch.setattr(json_utils.os, "replace", moved_by_another_save)
    assert json_utils.load_for_rewrite(path, "default", expected_type=dict) == "default"


def test_a_file_that_cant_be_read_for_a_moment_is_not_set_aside(tmp_path, monkeypatch):
    import builtins

    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    set_use_context(UseContext.PERSONAL, set_via="test", path=path)
    good = path.read_text()
    real_open = builtins.open

    def locked(file, *a, **kw):
        if Path(file) == path:
            raise PermissionError(13, "locked by another program")
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", locked)
    with pytest.raises(PermissionError):
        set_use_context(UseContext.RESEARCH, set_via="test", path=path)
    monkeypatch.undo()
    assert path.read_text() == good  # untouched: the save failed instead
    assert _corrupt_copies(path) == []


def test_unreadable_warning_fires_again_after_the_file_recovers(tmp_path, monkeypatch, caplog):
    import builtins

    from bpp.utils import json_utils

    path = tmp_path / "f.json"
    path.write_text("{}")
    real_open = builtins.open

    def flaky(file, *a, **kw):
        if Path(file) == path:
            raise PermissionError(13, "denied")
        return real_open(file, *a, **kw)

    def unreadable_warnings():
        return sum("Can't read" in r.getMessage() for r in caplog.records)

    for broken in (True, True, False, True):
        if broken:
            monkeypatch.setattr(builtins, "open", flaky)
        json_utils.load_json_file(path, {}, expected_type=dict, context="f")
        monkeypatch.undo()
    assert unreadable_warnings() == 2  # once, then again after it recovered


def test_adding_a_model_keeps_a_store_with_a_skipped_entry(tmp_path):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    original = json.dumps({"entries": [{"id": "byom_old", "display_name": 5}]})
    path.write_text(original)
    fp = tmp_path / "m.onnx"
    fp.write_bytes(b"model")
    byom.add_byom_entry(display_name="M", kind="face_embedder", file_path=fp, path=path)
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == original


def test_a_stored_model_with_a_blank_type_reads_as_the_default(tmp_path):
    from bpp.registry import byom

    fields = dict(
        id="byom_old",
        display_name="M",
        kind="",
        file_path="/m.onnx",
        weight_sha256="a" * 64,
        added_at="2026-10-14",
        ack_text_version="v1",
        ack_text_sha256="b" * 64,
    )
    path = tmp_path / "byom-models.json"
    path.write_text(json.dumps({"entries": [fields]}))
    (entry,) = byom._read_store(path=path).entries
    assert entry.kind == "face_embedder"


def test_saving_the_library_list_keeps_a_file_with_a_dropped_entry(tmp_path, monkeypatch):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    original = json.dumps({"libraries": [{"path": "/a"}, {"path": 7}], "active": None})
    path.write_text(original)
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    registry.add_library(str(tmp_path / "new"))
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == original


def test_saving_a_clean_library_list_keeps_no_copy(tmp_path, monkeypatch):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    registry.add_library(str(tmp_path / "a"))
    registry.set_active_library(str(tmp_path / "a"))
    registry.add_library(str(tmp_path / "b"))
    assert _corrupt_copies(path) == []


def test_a_model_added_with_a_blank_type_stays_visible(tmp_path):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    fp = tmp_path / "m.onnx"
    fp.write_bytes(b"model")
    byom.add_byom_entry(display_name="M", kind="  ", file_path=fp, path=path)
    (entry,) = byom._read_store(path=path).entries
    assert entry.kind == "face_embedder"


@pytest.mark.parametrize("value", [[1], {"a": 1}, 5])
def test_a_use_context_file_with_an_odd_value_can_still_be_saved(tmp_path, value):
    from bpp.registry.use_context_store import UseContext, set_use_context

    path = tmp_path / "use-context.json"
    original = json.dumps({"use_context": value})
    path.write_text(original)
    set_use_context(UseContext.PERSONAL, set_via="test", path=path)  # used to raise TypeError
    assert json.loads(path.read_text())["use_context"] == "personal"
    (copy,) = _corrupt_copies(path)
    assert (tmp_path / copy).read_text() == original


def test_a_failing_validity_check_keeps_a_copy_instead_of_blocking_the_save(tmp_path):
    from bpp.utils.json_utils import load_for_rewrite

    path = tmp_path / "f.json"
    path.write_text("{}")

    def broken(_):
        raise KeyError("x")

    assert load_for_rewrite(path, "default", expected_type=dict, is_valid=broken) == "default"
    assert len(_corrupt_copies(path)) == 1


@pytest.mark.parametrize(
    "store",
    [{"schema_version": "1"}, {"kind": " face_embedder "}],
    ids=["text-schema-version", "padded-kind"],
)
def test_a_byom_store_the_reader_rewrites_keeps_a_copy(tmp_path, store):
    from bpp.registry import byom

    entry = dict(
        id="byom_old",
        display_name="M",
        kind=store.get("kind", "face_embedder"),
        file_path="/m.onnx",
        weight_sha256="a" * 64,
        added_at="2026-10-14",
        ack_text_version="v1",
        ack_text_sha256="b" * 64,
    )
    raw = {"entries": [entry]}
    if "schema_version" in store:
        raw["schema_version"] = store["schema_version"]
    assert byom._store_is_intact(raw) is False
    assert byom._store_is_intact({"schema_version": 1, "entries": [{**entry, "kind": "x"}]})


def _journal(tmp_path, monkeypatch, content: str):
    from bpp.db import batch_rename

    journal = tmp_path / "rename-journal.json"
    journal.write_text(content)
    monkeypatch.setattr(batch_rename, "_journal_path", lambda lib: str(journal))
    return batch_rename, journal


def test_a_rename_journal_that_cant_be_read_now_is_kept_for_next_start(tmp_path, monkeypatch):
    import builtins

    batch_rename, journal = _journal(tmp_path, monkeypatch, '[{"old": "a", "new": "b"}]')
    real_open = builtins.open

    def locked(file, *a, **kw):
        if Path(file) == journal:
            raise PermissionError(13, "locked")
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", locked)
    assert batch_rename.recover_interrupted_rename(None, str(tmp_path)) == []
    monkeypatch.undo()
    assert journal.exists()
    assert _corrupt_copies(journal) == []


def test_a_rename_journal_with_a_bad_entry_reverts_the_rest_and_is_kept(tmp_path, monkeypatch):
    old, new = tmp_path / "a.jpg", tmp_path / "b.jpg"
    new.write_bytes(b"x")  # renamed on disk, DB still has the old name
    content = json.dumps([{"old": str(old), "new": str(new)}, {"old": 5}])
    batch_rename, journal = _journal(tmp_path, monkeypatch, content)
    reverted = batch_rename.recover_interrupted_rename(None, str(tmp_path))
    assert [e["old"] for e in reverted] == [str(old)]
    assert old.exists()
    (copy,) = _corrupt_copies(journal)
    assert (tmp_path / copy).read_text() == content


def test_a_clean_rename_journal_is_removed(tmp_path, monkeypatch):
    batch_rename, journal = _journal(tmp_path, monkeypatch, "[]")
    batch_rename.recover_interrupted_rename(None, str(tmp_path))
    assert not journal.exists()
    assert _corrupt_copies(journal) == []


def test_a_failed_revert_keeps_the_rename_journal_in_place(tmp_path, monkeypatch):
    old, new = tmp_path / "a.jpg", tmp_path / "b.jpg"
    new.write_bytes(b"x")
    batch_rename, journal = _journal(
        tmp_path, monkeypatch, json.dumps([{"old": str(old), "new": str(new)}])
    )

    def locked(src, dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(batch_rename.os, "rename", locked)
    assert batch_rename.recover_interrupted_rename(None, str(tmp_path)) == []
    assert journal.exists()  # retried next start
    assert _corrupt_copies(journal) == []
