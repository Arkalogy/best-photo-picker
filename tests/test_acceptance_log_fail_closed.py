"""A damaged line in the acceptance log never makes a withdrawn license
count again: a damaged line after a model's latest acceptance is treated
as a withdrawal (the user is asked again), for that model when the line
still names it, otherwise for every model accepted before it."""

from __future__ import annotations

import json

import pytest

from bpp.registry import acceptance_log


def _row(model_id="nudenet_320n", event="accept", **over) -> dict:
    row = {
        "model_id": model_id,
        "model_sha256": "a" * 64,
        "ack_text_version": "v2",
        "ack_text_sha256": "b" * 64,
        "use_context_text_version": "v1",
        "use_context_text_sha256": "c" * 64,
        "use_context_at_acceptance": "personal",
        "separate_rights_asserted": False,
        "terms_url": "https://example.invalid/LICENSE",
        "terms_retrieved_at": "2026-06-02",
        "accepted_at": "2026-10-14T12:00:00+00:00",
        "event": event,
    }
    row.update(over)
    return row


def _log(tmp_path, *lines: bytes):
    path = tmp_path / "model-acceptance.jsonl"
    path.write_bytes(b"".join(line + b"\n" for line in lines))
    return path


def _j(row: dict) -> bytes:
    return json.dumps(row).encode()


def test_a_damaged_withdrawal_naming_the_model_withdraws_it(tmp_path):
    damaged_revoke = _j(_row(event="revoke", schema_version="x"))
    path = _log(tmp_path, _j(_row()), damaged_revoke)
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False


def test_an_unreadable_line_withdraws_every_model_accepted_before_it(tmp_path):
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        _j(_row("ultralytics_yolov11n_pets")),
        b'{"model_id": "nude\xffnet_320n", "event": "rev',  # truncated + bad byte
    )
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False
    assert acceptance_log.has_accepted("ultralytics_yolov11n_pets", path=path) is False


def test_a_damaged_line_about_another_model_leaves_this_one_alone(tmp_path):
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        _j(_row("ultralytics_yolov11n_pets", event="revoke", schema_version="x")),
    )
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is True


def test_accepting_again_after_damage_counts(tmp_path):
    path = _log(tmp_path, _j(_row()), b"garbage", _j(_row()))
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is True


def test_damage_before_the_latest_acceptance_does_not_matter(tmp_path):
    path = _log(tmp_path, b"garbage", _j(_row()))
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is True


def test_the_load_gate_refuses_after_a_damaged_withdrawal(tmp_path, monkeypatch):
    from bpp.registry.acceptance import is_acceptance_valid_for
    from bpp.registry.model_registry import get_entry

    entry = get_entry("nudenet_320n")
    row = _row(ack_text_version=entry.ack_text_version, ack_text_sha256=entry.ack_text_sha256)
    path = _log(tmp_path, _j(row))
    assert is_acceptance_valid_for(entry, log_path=path) is True
    path = _log(tmp_path, _j(row), b"\xff\xfe damaged")
    assert is_acceptance_valid_for(entry, log_path=path) is False


def test_the_acceptance_list_agrees_with_the_gate(tmp_path, monkeypatch):
    from bpp.registry.model_registry import get_entry
    from bpp.web.app import create_app

    entry = get_entry("nudenet_320n")
    row = _row(ack_text_version=entry.ack_text_version, ack_text_sha256=entry.ack_text_sha256)
    path = _log(tmp_path, _j(row), b'{"model_id": "nudenet_320n", "ev')
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(path))
    workdir = tmp_path / "w"
    workdir.mkdir()
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    rows = app.test_client().get("/api/v1/model-registry/acceptance/list").get_json()["acceptances"]
    latest = [r for r in rows if r["model_id"] == "nudenet_320n"][-1]
    # Not current, and not "an older text" either: the picker shows it as
    # needing a license, not "Re-accept needed".
    assert (latest["current"], latest["needs_reaccept"]) == (False, False)
    body = app.test_client().get("/api/v1/model-registry/acceptance/list").get_json()
    assert body["withdrawn_by_damage"] == ["nudenet_320n"]


def test_latest_by_model_matches_the_single_lookup(tmp_path):
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        _j(_row("ultralytics_yolov11n_pets")),
        _j(_row("ultralytics_yolov11n_pets", event="revoke", schema_version="x")),
        _j(_row("lama")),
    )
    bulk = acceptance_log.latest_by_model(path=path)
    for model_id in ("nudenet_320n", "ultralytics_yolov11n_pets", "lama"):
        assert bulk[model_id] == acceptance_log.find_latest_for_model(model_id, path=path), model_id
    assert bulk["ultralytics_yolov11n_pets"].event == "revoke"


def test_a_withdrawal_after_damage_is_still_recorded(tmp_path):
    path = _log(tmp_path, _j(_row()), b"garbage")
    written = acceptance_log.append_revocation("nudenet_320n", path=path)
    assert written is not None and written.event == "revoke"
    events = [r.event for r in acceptance_log.iter_rows(path=path)]
    assert events == ["accept", "revoke"]


def test_re_accepting_after_a_truncated_last_line_counts_the_first_time(tmp_path):
    path = tmp_path / "model-acceptance.jsonl"
    path.write_bytes(_j(_row()) + b"\n" + b'{"model_id": "nudenet_320n", "ev')  # no newline
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False
    row = acceptance_log.AcceptanceRow.from_dict(_row())
    acceptance_log.append_row(row, path=path)
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is True


def test_a_row_with_non_text_fields_counts_as_damaged(tmp_path):
    path = _log(tmp_path, _j(_row()), _j(_row(model_id=None, event="revoke")))
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False


@pytest.mark.parametrize(
    "over",
    [
        {"separate_rights_asserted": "false"},
        {"separate_rights_asserted": 0},
        {"checkbox_responses": {"box": "yes"}},
        {"event": 1},
        {"schema_version": "2"},
    ],
)
def test_a_wrong_type_field_is_damage_never_coerced(tmp_path, over):
    # bool("false") is True: coercing would grant commercial rights.
    path = _log(tmp_path, _j(_row()), _j(_row(**over)))
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False
    assert len(acceptance_log.list_acceptances(path=path)) == 1


def test_a_torn_line_that_still_names_its_model_only_withdraws_that_model(tmp_path):
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        b'{"model_id": "ultralytics_yolov11n_pets", "model_sha2',  # power cut mid-write
    )
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is True
    assert acceptance_log.has_accepted("ultralytics_yolov11n_pets", path=path) is False


def test_the_withdrawal_warning_is_logged_once_per_file_version(tmp_path, caplog):
    path = _log(tmp_path, _j(_row()), b"garbage")
    for _ in range(50):
        acceptance_log.has_accepted("nudenet_320n", path=path)
    withdrawn = [r for r in caplog.records if "treating it as withdrawn" in r.getMessage()]
    assert len(withdrawn) == 1


def test_the_snapshot_names_models_withdrawn_by_damage(tmp_path):
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        _j(_row("lama", event="revoke")),
        b"garbage",
    )
    snap = acceptance_log.snapshot(path=path)
    assert snap.withdrawn_by_damage == ("nudenet_320n",)  # lama was withdrawn for real
    assert snap.gate_view["nudenet_320n"].event == "revoke"
    assert len(snap.rows) == 2


def test_a_torn_line_with_another_row_glued_on_withdraws_everything(tmp_path):
    # Older builds could glue the next row onto a torn one: the first id on
    # the line may not be the torn row's, so no single model is blamed.
    torn_withdrawal_of_a = b'{"model_sha256": "aaa", "ev'
    path = _log(
        tmp_path,
        _j(_row("nudenet_320n")),
        _j(_row("lama")),
        torn_withdrawal_of_a + _j(_row("lama")),
    )
    assert acceptance_log.has_accepted("nudenet_320n", path=path) is False
    assert acceptance_log.has_accepted("lama", path=path) is False
