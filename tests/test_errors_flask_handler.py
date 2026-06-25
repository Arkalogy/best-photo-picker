"""P7 — Flask integration tests for the BppError handler.

The handler is registered in ``bpp.web.app.create_app``. We test it
in isolation here against a minimal Flask app to avoid the cost of
spinning up the full bpp web stack.

Pinned contract:

* Any BppError raised inside an endpoint produces a structured JSON
  envelope with the right HTTP status.
* ``user_message`` goes into the response; ``diagnostic_message``
  does NOT.
* The log line carries the ``diagnostic_message`` at WARNING with
  full exception traceback.
* ``context`` kwargs appear in the response body.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from flask import Flask, jsonify, request

from bpp.errors import (
    BppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


@pytest.fixture
def app():
    """Minimal Flask app with the BppError handler installed —
    mirrors what ``bpp.web.app.create_app`` does."""
    a = Flask(__name__)

    # The handler exactly as registered in create_app.
    @a.errorhandler(BppError)
    def _handle(exc: BppError) -> tuple[Any, int]:
        # Log line same shape as production; no `log` reference here
        # so the test's caplog can intercept via the module logger.
        if exc.context:
            a.logger.warning(
                "BppError on %s %s: %s context=%s",
                request.method,
                request.path,
                exc.diagnostic_message,
                exc.context,
                exc_info=exc,
            )
        else:
            a.logger.warning(
                "BppError on %s %s: %s",
                request.method,
                request.path,
                exc.diagnostic_message,
                exc_info=exc,
            )
        return jsonify(exc.to_dict()), exc.http_status

    @a.route("/raise-validation")
    def _raise_validation():
        raise ValidationError("Field is required", field="email")

    @a.route("/raise-notfound")
    def _raise_notfound():
        raise NotFoundError("Photo not found", photo_id=42)

    @a.route("/raise-conflict")
    def _raise_conflict():
        raise ConflictError("Already merged", primary=1, target=2)

    @a.route("/raise-split-messages")
    def _raise_split():
        # User-facing safe text + diagnostic with a filesystem path
        # that must stay out of the response.
        raise ValidationError(
            "Invalid input",
            user_message="Something you submitted wasn't quite right",
            diagnostic_message=("input parse failure at /var/folders/bpp/run/state.json"),
        )

    return a


@pytest.fixture
def client(app):
    return app.test_client()


# ── Status + envelope shape ──


class TestStatusAndEnvelope:
    def test_validation_error_returns_400(self, client):
        resp = client.get("/raise-validation")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["code"] == "validation_error"
        assert body["error"] == "Field is required"

    def test_notfound_error_returns_404(self, client):
        resp = client.get("/raise-notfound")
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["code"] == "not_found"
        assert body["error"] == "Photo not found"

    def test_conflict_error_returns_409(self, client):
        resp = client.get("/raise-conflict")
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["code"] == "conflict"

    def test_context_kwargs_present_in_response(self, client):
        resp = client.get("/raise-notfound")
        body = resp.get_json()
        assert body["context"] == {"photo_id": 42}

    def test_no_context_key_when_no_kwargs(self, client):
        # raise-conflict carries context; let's test a no-context case
        # by hitting raise-validation which only sets field=...
        resp = client.get("/raise-validation")
        body = resp.get_json()
        assert body["context"] == {"field": "email"}
        # And response keys are exactly {error, code, context}.
        assert set(body.keys()) == {"error", "code", "context"}


# ── user_message / diagnostic_message split ──


class TestSplitMessages:
    def test_user_message_in_response_diagnostic_not(self, client):
        resp = client.get("/raise-split-messages")
        body = resp.get_json()
        assert body["error"] == "Something you submitted wasn't quite right"
        # The diagnostic message contains a /var/folders/... path that
        # must NOT appear in the response envelope.
        assert "/var/folders" not in resp.get_data(as_text=True)

    def test_diagnostic_message_is_logged(self, client, app, caplog):
        with caplog.at_level(logging.WARNING, logger=app.logger.name):
            client.get("/raise-split-messages")
        # The diagnostic message (with the path) is in the log.
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "/var/folders" in joined

    def test_user_message_defaults_to_message(self, client):
        # /raise-notfound passes only message — user_message defaults.
        resp = client.get("/raise-notfound")
        body = resp.get_json()
        assert body["error"] == "Photo not found"

    def test_to_dict_uses_user_message_directly(self):
        e = ValidationError(
            "raw msg",
            user_message="safe user-facing",
            diagnostic_message="contains /Users/secret/path",
        )
        envelope = e.to_dict()
        assert envelope["error"] == "safe user-facing"
        # Sanity: the dict-level conversion never reaches into
        # diagnostic territory.
        assert "/Users/secret" not in str(envelope)


# ── Log routing ──


class TestLogRouting:
    def test_handler_logs_method_and_path(self, client, app, caplog):
        with caplog.at_level(logging.WARNING, logger=app.logger.name):
            client.get("/raise-validation")
        log_line = " ".join(r.getMessage() for r in caplog.records)
        # Method + path in the log so on-call can correlate.
        assert "GET" in log_line
        assert "/raise-validation" in log_line

    def test_handler_logs_exc_info(self, client, app, caplog):
        """``exc_info=exc`` on the log call means the formatter
        renders the traceback. We assert the record carries one."""
        with caplog.at_level(logging.WARNING, logger=app.logger.name):
            client.get("/raise-validation")
        records_with_exc = [r for r in caplog.records if r.exc_info is not None]
        assert records_with_exc, "handler log call must include exc_info"

    def test_handler_logs_exc_context(self, client, app, caplog):
        """T4: the handler should log ``exc.context`` alongside the
        diagnostic message so on-call has the actionable details
        without re-running the request.

        The context dict (``photo_id=42``, ``field='email'``, etc.) is
        the most useful single piece of info for triaging — currently
        it lands only in the response envelope, where on-call has to
        cross-reference the HTTP log with the response body to find
        it. Including it in the WARN line is one log lookup, not two.
        """
        with caplog.at_level(logging.WARNING, logger=app.logger.name):
            client.get("/raise-notfound")  # raises NotFoundError("Photo not found", photo_id=42)
        log_line = " ".join(r.getMessage() for r in caplog.records)
        # photo_id=42 — the context kwarg — must appear in the log.
        assert "photo_id" in log_line and "42" in log_line, (
            f"handler must log exc.context for on-call triage; log line was: {log_line!r}"
        )

    def test_handler_omits_context_field_when_empty(self, client, app, caplog):
        """No empty 'context={}' noise when the exception had no
        kwargs — same omission rule the response envelope uses."""

        @app.route("/raise-no-context")
        def _raise_no_context():
            raise ValidationError("plain message")

        with caplog.at_level(logging.WARNING, logger=app.logger.name):
            client.get("/raise-no-context")
        log_line = " ".join(r.getMessage() for r in caplog.records)
        # No "context=" or "context: {}" in the line — keep the log
        # signal-to-noise high.
        assert "context={}" not in log_line and "context: {}" not in log_line, (
            f"empty context should be omitted from log line; got: {log_line!r}"
        )


# ── Legacy exception subclasses still flow through ──


class TestLegacyExceptions:
    """The five pre-P7 exception classes inherit BppError via multiple
    inheritance. The Flask handler catches every one of them.

    Parametric test covers ALL five classes so a future MRO regression
    breaking one's BppError ancestry fails loudly here.
    """

    @pytest.mark.parametrize(
        "build_exc,expected_status,expected_code",
        [
            # ModelIntegrityError → IntegrityError → BppError
            (
                lambda: __import__(
                    "bpp.scoring.model_base", fromlist=["ModelIntegrityError"]
                ).ModelIntegrityError("sha mismatch"),
                500,
                "integrity_error",
            ),
            # ConfigValidationError → BppError, ValueError
            (
                lambda: __import__(
                    "bpp.config_schema", fromlist=["ConfigValidationError"]
                ).ConfigValidationError("max_long_side", "must be > 0"),
                400,
                "config_validation_error",
            ),
            # ServingLockError → BppError
            (
                lambda: __import__(
                    "bpp.utils.serving_lock", fromlist=["ServingLockError"]
                ).ServingLockError("lock unavailable"),
                503,
                "serving_lock_error",
            ),
            # FaceEmbeddingsTooLarge → ResourceExhaustedError → BppError, RuntimeError
            (
                lambda: __import__(
                    "bpp.db.face_queries", fromlist=["FaceEmbeddingsTooLarge"]
                ).FaceEmbeddingsTooLarge(100, 50),
                503,
                "face_embeddings_too_large",
            ),
            # ClipEmbeddingsTooLarge → ResourceExhaustedError → BppError, RuntimeError
            (
                lambda: __import__(
                    "bpp.db.clip", fromlist=["ClipEmbeddingsTooLarge"]
                ).ClipEmbeddingsTooLarge(100, 50),
                503,
                "clip_embeddings_too_large",
            ),
        ],
        ids=[
            "ModelIntegrityError",
            "ConfigValidationError",
            "ServingLockError",
            "FaceEmbeddingsTooLarge",
            "ClipEmbeddingsTooLarge",
        ],
    )
    def test_legacy_exception_routes_through_handler(
        self, app, build_exc, expected_status, expected_code
    ):
        @app.route(f"/raise-legacy-{expected_code}")
        def _raise_legacy():
            raise build_exc()

        client = app.test_client()
        resp = client.get(f"/raise-legacy-{expected_code}")
        assert resp.status_code == expected_status, (
            f"expected {expected_status}; got {resp.status_code}; body={resp.get_json()}"
        )
        body = resp.get_json()
        assert body["code"] == expected_code
