"""R8-M6: @validate_json decorator behavior.

Lock the contract so future changes to the decorator can't quietly
break the dozens of endpoints that may opt in over time.

This commit ships the decorator + migrates `api_photos_delete`
and `api_photos_restore` as worked examples. The unified
response envelope (the second half of M6) is deliberately NOT
shipped — that requires a coordinated frontend change. The
decorator is a one-way infrastructure addition: existing
endpoints keep working unchanged, new code can opt in.
"""

from __future__ import annotations

from flask import Flask, jsonify

from bpp.web.request_validation import field, validate_json


def _app_for(view):
    """Build a one-route Flask app for end-to-end decorator tests."""
    app = Flask(__name__)
    app.add_url_rule("/x", view_func=view, methods=["POST"])
    return app


class TestRequiredKey:
    def test_passes_when_present(self):
        @validate_json(name=field())
        def _view(*, name):
            return jsonify({"got": name}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"name": "alice"})
        assert r.status_code == 200
        assert r.get_json() == {"got": "alice"}

    def test_400_when_missing(self):
        @validate_json(name=field())
        def _view(*, name):
            return jsonify({"got": name}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={})
        assert r.status_code == 400
        assert r.get_json()["error"] == "name required"

    def test_custom_error_message(self):
        @validate_json(
            confirmation=field(error_message="confirmation='delete' required"),
        )
        def _view(*, confirmation):
            return jsonify({"ok": True}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={})
        assert r.status_code == 400
        assert r.get_json()["error"] == "confirmation='delete' required"


class TestOptionalKey:
    def test_default_used_when_missing(self):
        @validate_json(limit=field(default=50))
        def _view(*, limit):
            return jsonify({"limit": limit}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={})
        assert r.get_json() == {"limit": 50}

    def test_caller_value_overrides_default(self):
        @validate_json(limit=field(default=50))
        def _view(*, limit):
            return jsonify({"limit": limit}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"limit": 100})
        assert r.get_json() == {"limit": 100}

    def test_explicit_none_default_is_optional(self):
        """`field(default=None)` is OPTIONAL even though None is
        falsy — the sentinel for required is a unique object, not
        None, so callers can use None as a legitimate default."""

        @validate_json(album_id=field(default=None))
        def _view(*, album_id):
            return jsonify({"album_id": album_id}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={})
        assert r.status_code == 200
        assert r.get_json() == {"album_id": None}


class TestTypeCoercion:
    def test_int_coercion(self):
        @validate_json(limit=field(default=50, type=int))
        def _view(*, limit):
            return jsonify({"limit": limit, "type": type(limit).__name__}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"limit": "100"})
        assert r.get_json() == {"limit": 100, "type": "int"}

    def test_int_coercion_failure_returns_400(self):
        @validate_json(limit=field(default=50, type=int))
        def _view(*, limit):
            return jsonify({"limit": limit}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"limit": "fifty"})
        assert r.status_code == 400


class TestContainerCoercion:
    """R9-extensibility-M2: container target types refuse non-matching
    inputs up front. Pre-fix, ``type=list`` accepted a JSON string
    and silently produced ``['h','e','l','l','o']`` because Python's
    ``list("hello")`` is "list of characters." The decorator now
    requires the input to already be the right shape; the JSON parser
    is the layer that does the conversion."""

    def test_list_target_accepts_array(self):
        @validate_json(items=field(type=list))
        def _view(*, items):
            return jsonify({"items": items}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"items": ["a", "b"]})
        assert r.status_code == 200
        assert r.get_json() == {"items": ["a", "b"]}

    def test_list_target_rejects_string(self):
        """Pre-fix: ``list("hello")`` quietly became character list."""

        @validate_json(items=field(type=list))
        def _view(*, items):
            return jsonify({"items": items}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"items": "hello"})
        assert r.status_code == 400, (
            "string-where-list-expected used to silently coerce to "
            "['h','e','l','l','o']; must now 400"
        )

    def test_dict_target_accepts_object(self):
        @validate_json(opts=field(type=dict))
        def _view(*, opts):
            return jsonify({"opts": opts}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"opts": {"k": "v"}})
        assert r.get_json() == {"opts": {"k": "v"}}

    def test_dict_target_rejects_list(self):
        @validate_json(opts=field(type=dict))
        def _view(*, opts):
            return jsonify({"opts": opts}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x", json={"opts": [["k", "v"]]})
        assert r.status_code == 400, (
            "list-of-pairs used to silently become a dict via "
            "Python's permissive dict() ctor; must now 400"
        )


class TestMissingBody:
    def test_no_body_treated_as_empty_dict(self):
        """A POST with no JSON body and a required key behaves
        identically to a POST with `{}`."""

        @validate_json(name=field())
        def _view(*, name):
            return jsonify({"got": name}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x")
        assert r.status_code == 400
        assert "required" in r.get_json()["error"]

    def test_no_body_with_all_optional_keys(self):
        @validate_json(name=field(default="anon"))
        def _view(*, name):
            return jsonify({"got": name}), 200

        client = _app_for(_view).test_client()
        r = client.post("/x")
        assert r.status_code == 200
        assert r.get_json() == {"got": "anon"}


class TestKeywordInjection:
    """Validated values arrive as keyword args. Positional path
    params (e.g. /api/v1/albums/<int:album_id>) keep their normal
    Flask injection — the decorator's kwargs append to whatever
    Flask passes."""

    def test_path_params_coexist_with_validated_kwargs(self):
        app = Flask(__name__)

        @validate_json(name=field())
        def _view(album_id, *, name):
            return jsonify({"album_id": album_id, "name": name}), 200

        app.add_url_rule("/album/<int:album_id>", view_func=_view, methods=["POST"])
        client = app.test_client()
        r = client.post("/album/42", json={"name": "vacation"})
        assert r.status_code == 200
        assert r.get_json() == {"album_id": 42, "name": "vacation"}


class TestValidationLogging:
    """R9-supportability-M1: a 400 from `@validate_json` must leave a
    log breadcrumb so an operator debugging "user got 400" can find
    which route + which rule fired without re-running the request.
    The body is NOT logged — payloads can carry filepaths or
    tokens that don't belong in server.log."""

    def test_400_emits_warning_with_path(self, caplog):
        @validate_json(name=field())
        def _view(*, name):
            return jsonify({}), 200

        client = _app_for(_view).test_client()
        with caplog.at_level("WARNING", logger="bpp.web.request_validation"):
            r = client.post("/x", json={})
        assert r.status_code == 400

        # The log line must include both the path and the rule
        # message (so an operator searching for "name required"
        # finds the route).
        msgs = [rec.message for rec in caplog.records]
        assert any("/x" in m and "name required" in m for m in msgs), (
            f"validate_json 400 must log path + rule; got {msgs}"
        )

    def test_400_does_not_log_request_body(self, caplog):
        """Operators must not learn payload contents from a 400
        log line — payloads can contain sensitive values."""

        @validate_json(filepath=field())
        def _view(*, filepath):
            return jsonify({}), 200

        client = _app_for(_view).test_client()
        sentinel = "/secret/path/with/identifying/info.jpg"
        with caplog.at_level("WARNING", logger="bpp.web.request_validation"):
            client.post("/x", json={"some_other_field": sentinel})

        # The body sent was {"some_other_field": "/secret/path/..."}
        # — that exact value must NOT appear in any log record.
        for rec in caplog.records:
            assert sentinel not in rec.message


class TestRealEndpointMigration:
    """Source-scan: assert the two example migrations
    (api_photos_delete + api_photos_restore) actually use the
    decorator. Future audits flagging "endpoint X should migrate"
    can lock that progress here."""

    def test_photos_delete_uses_validate_json(self):
        from pathlib import Path

        src = Path("bpp/web/bp_photos_lifecycle.py").read_text()
        # Find api_photos_delete and confirm it has @validate_json
        # immediately above
        idx = src.index("def api_photos_delete(")
        head = src[max(0, idx - 200) : idx]
        assert "@validate_json" in head

    def test_photos_restore_uses_validate_json(self):
        from pathlib import Path

        src = Path("bpp/web/bp_photos_lifecycle.py").read_text()
        idx = src.index("def api_photos_restore(")
        head = src[max(0, idx - 200) : idx]
        assert "@validate_json" in head
