"""A JSON file or column with valid syntax but the wrong shape (``[]``,
``1``, a string) falls back to its safe default instead of crashing the
caller with ``AttributeError`` — most importantly the use-context file,
which every model license check reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bpp.utils.json_utils import safe_json_loads

# ── the shared reader ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "default", "kwargs", "expected"),
    [
        ("[]", {}, {"expected_type": dict}, {}),
        ("{}", [], {"expected_type": list}, []),
        ('"x"', {}, {"expected_type": dict}, {}),
        ('{"a": 1}', {}, {"expected_type": dict}, {"a": 1}),
        ("[1]", [], {"expected_type": list}, [1]),
        ("[1]", None, {"expected_type": dict}, None),
        (b"\xff\xfe", {}, {"expected_type": dict}, {}),  # non-UTF-8 bytes
        ("7", None, {"expected_type": object}, 7),  # object: any shape is fine
        ("[]", {}, {"expected_type": list}, []),  # explicit, never guessed from the default
    ],
)
def test_wrong_shape_falls_back_to_the_default(text, default, kwargs, expected):
    assert safe_json_loads(text, default, **kwargs) == expected


# ── files on disk ────────────────────────────────────────────────────


@pytest.mark.parametrize("content", ["[]", "1", '"commercial"', "null"])
def test_a_wrong_shape_use_context_file_reprompts(tmp_path, monkeypatch, content):
    from bpp.registry.use_context_store import UseContext, get_use_context

    path = tmp_path / "use-context.json"
    path.write_text(content)
    monkeypatch.setenv("BPP_USE_CONTEXT_PATH", str(path))
    assert get_use_context() is UseContext.UNSPECIFIED


def test_a_use_context_file_with_bad_fields_still_loads(tmp_path, monkeypatch):
    from bpp.registry.use_context_store import UseContext, read_record

    path = tmp_path / "use-context.json"
    path.write_text(json.dumps({"use_context": "personal", "history": 5, "schema_version": "x"}))
    rec = read_record(path=path)
    assert rec.use_context is UseContext.PERSONAL
    assert rec.history == ()


@pytest.mark.parametrize(
    "content",
    ["[]", '{"entries": {"a": 1}}', '{"entries": [1, "x", null]}'],
)
def test_a_wrong_shape_byom_store_reads_as_empty(tmp_path, monkeypatch, content):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    path.write_text(content)
    assert byom._read_store(path=path).entries == ()


@pytest.mark.parametrize("content", ["[]", '{"libraries": "x"}', '"x"'])
def test_a_wrong_shape_library_registry_is_the_default(tmp_path, monkeypatch, content):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    path.write_text(content)
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    data = registry.load_registry()
    assert isinstance(data["libraries"], list)


def test_a_wrong_shape_license_log_line_is_skipped(tmp_path):
    from bpp.registry import acceptance_log

    path = tmp_path / "model-acceptance.jsonl"
    path.write_text("[1, 2]\n7\n")
    assert list(acceptance_log.iter_rows(path=path)) == []


@pytest.mark.parametrize("content", ['{"old": "a"}', '[1, "x", {"old": null}]'])
def test_a_wrong_shape_rename_journal_reverts_nothing(tmp_path, monkeypatch, content):
    from bpp.db import batch_rename

    journal = tmp_path / "rename-journal.json"
    journal.write_text(content)
    monkeypatch.setattr(batch_rename, "_journal_path", lambda lib: str(journal))
    assert batch_rename.recover_interrupted_rename(None, str(tmp_path)) == []


def test_wrong_shape_legacy_imports_import_nothing(tmp_path):
    from bpp.db import migrate

    analysis = tmp_path / "analysis.json"
    analysis.write_text('{"not": "a list"}')
    presets = tmp_path / "presets.json"
    presets.write_text("[1, 2]")
    assert migrate.import_from_analysis_json(None, str(analysis)) == 0
    assert migrate.import_presets_from_json(None, str(presets)) == 0


@pytest.fixture(autouse=True)
def _no_real_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BPP_BYOM_PATH", str(tmp_path / "byom-default.json"))


def test_a_wrong_shape_album_config_or_rule_reads_as_none(tmp_path):
    from bpp.db.albums import get_album
    from bpp.db.connection import init_db

    conn = init_db(str(tmp_path / "photopicker.db"))
    cur = conn.execute(
        "INSERT INTO albums (name, config_json, rule_json) VALUES (?, ?, ?)",
        ("A", "[1, 2]", '"x"'),
    )
    conn.commit()
    album = get_album(conn, cur.lastrowid)
    assert album["config"] is None and album["rule"] is None


def test_a_wrong_shape_analysis_cache_row_is_a_miss(tmp_path):
    import sqlite3

    from bpp.scoring.aggregate import _get_cached, init_analysis_db

    db = str(tmp_path / "analysis.db")
    init_analysis_db(db)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO image_analysis (filepath, file_size, file_mtime, result_json) "
            "VALUES (?, ?, ?, ?)",
            ("/p/a.jpg", 1, 2.0, "[1, 2]"),
        )
    assert _get_cached(db, "/p/a.jpg", 1, 2.0) is None


# ── non-text bytes, and the remaining shapes ─────────────────────────

_GARBAGE = b"\xff\xfe\x00garbage"


def test_a_non_text_use_context_file_reprompts(tmp_path):
    from bpp.registry.use_context_store import UseContext, read_record

    path = tmp_path / "use-context.json"
    path.write_bytes(_GARBAGE)
    assert read_record(path=path).use_context is UseContext.UNSPECIFIED


def test_a_non_text_byom_store_reads_as_empty(tmp_path):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    path.write_bytes(_GARBAGE)
    assert byom._read_store(path=path).entries == ()


def test_a_non_text_license_log_line_is_skipped(tmp_path):
    from bpp.registry import acceptance_log

    path = tmp_path / "model-acceptance.jsonl"
    path.write_bytes(_GARBAGE + b"\n")
    assert list(acceptance_log.iter_rows(path=path)) == []


def test_a_non_text_backup_info_file_is_ignored(tmp_path):
    from bpp.db import backup

    db = tmp_path / "photopicker.db.backup"
    db.write_bytes(b"x")
    (tmp_path / "photopicker.db.backup.meta.json").write_bytes(_GARBAGE)
    assert backup.read_backup_meta(str(db)) is None


def test_a_bad_byom_schema_version_still_loads(tmp_path):
    from bpp.registry import byom

    path = tmp_path / "byom-models.json"
    path.write_text('{"schema_version": "x", "entries": []}')
    assert byom._read_store(path=path).entries == ()


def test_malformed_library_entries_are_dropped(tmp_path, monkeypatch):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    path.write_text(json.dumps({"libraries": [1, {"name": "x"}, {"path": "/ok", "name": "OK"}]}))
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    assert [lib["path"] for lib in registry.load_registry()["libraries"]] == ["/ok"]


def test_malformed_use_context_history_items_are_dropped(tmp_path):
    from bpp.registry.use_context_store import read_record

    path = tmp_path / "use-context.json"
    path.write_text(json.dumps({"use_context": "personal", "history": [1, "x", {"a": 1}]}))
    assert read_record(path=path).history == ({"a": 1},)


def test_wrong_shape_redeye_and_exif_read_as_missing(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.edits import get_photo_edits, save_photo_edits
    from bpp.web.photo_dict import build_photo_dict

    conn = init_db(str(tmp_path / "photopicker.db"))
    pid = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES ('/p/a.jpg', 'a.jpg', 1, 1.0)"
    ).lastrowid
    save_photo_edits(conn, pid, {"brightness": 1.0})
    conn.execute("UPDATE photo_edits SET redeye_json = '{\"x\": 1}' WHERE photo_id = ?", (pid,))
    conn.commit()
    assert not get_photo_edits(conn, pid)["redeye_points"]
    photo = build_photo_dict({"filepath": "/p/a.jpg", "exif_json": "[1, 2]"}, None)
    assert not photo.get("exif")


def test_the_v31_migration_skips_a_wrong_shape_config(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.migrations_recent import _migrate_v31

    conn = init_db(str(tmp_path / "photopicker.db"))
    conn.execute("INSERT INTO albums (name, config_json) VALUES ('A', '[1, 2]')")
    conn.commit()
    _migrate_v31(conn)  # must not raise
    assert conn.execute("SELECT config_json FROM albums WHERE name='A'").fetchone()[0] == "[1, 2]"


def test_every_caller_states_the_shape_it_expects():
    """Rule: every safe_json_loads / load_json_file call names its
    expected_type, so a wrong-shape value can't reach code that assumes
    one. (A call that truly accepts any shape passes expected_type=object.)"""
    import re

    root = Path(__file__).resolve().parents[1] / "bpp"
    missing = []
    for py in root.rglob("*.py"):
        if py.name == "json_utils.py":
            continue
        text = py.read_text()
        for m in re.finditer(r"\b(safe_json_loads|load_json_file)\(", text):
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            args = text[m.end() : i - 1].strip()
            # Skip prose like `safe_json_loads()`; None would disable the check.
            if args and ("expected_type" not in args or "expected_type=None" in args):
                missing.append(f"{py.relative_to(root)}:{text[: m.start()].count(chr(10)) + 1}")
    assert not missing, missing


def _row(**over) -> dict:
    row = {
        "model_id": "nudenet_320n",
        "model_sha256": "a" * 64,
        "ack_text_version": "v2",
        "ack_text_sha256": "b" * 64,
        "use_context_text_version": "v1",
        "use_context_text_sha256": "c" * 64,
        "use_context_at_acceptance": "personal",
        "separate_rights_asserted": False,
        "terms_url": "https://example.invalid/LICENSE",
        "terms_permalink_url": "https://example.invalid/abc/LICENSE",
        "terms_retrieved_at": "2026-06-02",
        "accepted_at": "2026-10-14T12:00:00+00:00",
    }
    row.update(over)
    return row


def test_the_sample_row_is_valid(tmp_path):
    from bpp.registry import acceptance_log

    path = tmp_path / "model-acceptance.jsonl"
    path.write_text(json.dumps(_row()) + "\n")
    assert [r.model_id for r in acceptance_log.iter_rows(path=path)] == ["nudenet_320n"]


def test_a_license_log_line_with_a_bad_number_is_skipped(tmp_path):
    from bpp.registry import acceptance_log

    path = tmp_path / "model-acceptance.jsonl"
    path.write_text(json.dumps(_row(schema_version="x")) + "\n" + json.dumps(_row()) + "\n")
    assert [r.model_id for r in acceptance_log.iter_rows(path=path)] == ["nudenet_320n"]


def test_a_license_log_line_with_damaged_text_is_skipped_not_altered(tmp_path):
    from bpp.registry import acceptance_log

    path = tmp_path / "model-acceptance.jsonl"
    damaged = json.dumps(_row(model_id="nudeZnet_320n")).encode().replace(b"Z", b"\xff")
    path.write_bytes(damaged + b"\n" + json.dumps(_row()).encode() + b"\n")
    assert [r.model_id for r in acceptance_log.iter_rows(path=path)] == ["nudenet_320n"]


def test_a_non_text_rename_journal_is_kept_as_a_copy(tmp_path, monkeypatch):
    from bpp.db import batch_rename

    journal = tmp_path / "rename-journal.json"
    journal.write_bytes(_GARBAGE)
    monkeypatch.setattr(batch_rename, "_journal_path", lambda lib: str(journal))
    assert batch_rename.recover_interrupted_rename(None, str(tmp_path)) == []
    assert not journal.exists()
    (copy,) = tmp_path.glob("rename-journal.json.corrupt-*")
    assert copy.read_bytes() == _GARBAGE


@pytest.mark.parametrize(
    "entry", [{"old": "a.jpg", "new": [1]}, {"old": {}, "new": "B"}, {"old": "a.jpg", "new": 5}]
)
def test_rename_journal_entries_need_string_paths(tmp_path, monkeypatch, entry):
    from bpp.db import batch_rename

    existing = tmp_path / "b.jpg"
    existing.write_bytes(b"x")  # the "new" path exists, so only the type check stops it
    entry = {k: (str(existing) if v == "B" else v) for k, v in entry.items()}
    journal = tmp_path / "rename-journal.json"
    journal.write_text(json.dumps([entry]))
    monkeypatch.setattr(batch_rename, "_journal_path", lambda lib: str(journal))
    assert batch_rename.recover_interrupted_rename(None, str(tmp_path)) == []


@pytest.mark.parametrize("active", [["x"], 5, {"a": 1}])
def test_a_wrong_shape_active_library_is_none(tmp_path, monkeypatch, active):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    path.write_text(json.dumps({"libraries": [], "active": active}))
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    assert registry.load_registry()["active"] is None


def test_library_list_sorts_with_mixed_last_opened(tmp_path, monkeypatch):
    from bpp.db import registry

    path = tmp_path / "libraries.json"
    path.write_text(
        json.dumps(
            {
                "libraries": [
                    {"path": "/a", "last_opened": None},
                    {"path": "/b", "last_opened": "2026"},
                ]
            }
        )
    )
    monkeypatch.setattr(registry, "get_registry_path", lambda: str(path))
    assert [lib["path"] for lib in registry.list_libraries()] == ["/b", "/a"]


def test_byom_entries_with_non_text_fields_are_skipped(tmp_path):
    from bpp.registry import byom

    fields = dict(
        display_name="M",
        kind="face_embedder",
        file_path="/m.onnx",
        weight_sha256="a" * 64,
        added_at="2026-10-14",
        ack_text_version="v1",
        ack_text_sha256="b" * 64,
    )
    path = tmp_path / "byom-models.json"
    path.write_text(json.dumps({"entries": [{"id": ["x"], **fields}, {"id": "byom_ok", **fields}]}))
    assert [e.id for e in byom._read_store(path=path).entries] == ["byom_ok"]


@pytest.mark.parametrize(
    "value", [5, memoryview(b"{}"), b"[" * 200000], ids=["int", "memoryview", "deep"]
)
def test_the_reader_never_raises_on_odd_input(value):
    assert safe_json_loads(value, {}, expected_type=dict) == {}


def test_expected_type_is_required():
    with pytest.raises(TypeError):
        safe_json_loads("{}", {})  # type: ignore[call-arg]


def test_pet_and_tag_album_refresh_survive_a_list_rule(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.smart_album_pets import _refresh_pet_albums
    from bpp.db.smart_album_tags import _refresh_tag_albums

    conn = init_db(str(tmp_path / "photopicker.db"))
    for kind in ("smart_pet", "smart_tag"):
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, ?, '[1, 2]')",
            (kind, kind),
        )
    conn.commit()
    _refresh_pet_albums(conn)  # used to raise AttributeError on .get
    _refresh_tag_albums(conn)


def test_an_already_decoded_value_of_the_right_shape_is_kept():
    assert safe_json_loads({"a": 1}, {}, expected_type=dict) == {"a": 1}
    assert safe_json_loads([1], {}, expected_type=dict) == {}


def test_a_damaged_file_warns_once_until_it_reads_cleanly(tmp_path, caplog):
    from bpp.utils.json_utils import load_json_file

    path = tmp_path / "f.json"
    for content in ("[]", "{bad", "[]", "{}", "[]"):
        path.write_text(content)
        load_json_file(path, {}, expected_type=dict, context="f")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2, [r.getMessage() for r in warnings]  # once, then again after "{}"
