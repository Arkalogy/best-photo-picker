"""Tests for NAS/network storage resilience: retry, health check, recheck-missing."""

from __future__ import annotations

import errno
from unittest.mock import patch

import pytest

from bpp.utils.retry import (
    check_storage_accessible,
    is_transient,
    retry_io,
)


class TestIsTransient:
    def test_eio(self):
        assert is_transient(OSError(errno.EIO, "I/O error"))

    def test_estale(self):
        assert is_transient(OSError(116, "Stale file handle"))

    def test_etimedout(self):
        assert is_transient(OSError(110, "Connection timed out"))

    def test_ehostdown(self):
        assert is_transient(OSError(112, "Host is down"))

    def test_normal_enoent_not_transient(self):
        assert not is_transient(OSError(errno.ENOENT, "No such file"))

    def test_message_based_detection(self):
        e = OSError("stale file handle on NAS")
        e.errno = None
        assert is_transient(e)

    def test_connection_reset_message(self):
        e = OSError("connection reset by peer")
        e.errno = None
        assert is_transient(e)

    def test_normal_message_not_transient(self):
        e = OSError("not a directory")
        e.errno = None
        assert not is_transient(e)


class TestRetryIo:
    def test_succeeds_first_try(self):
        result = retry_io(lambda: 42)
        assert result == 42

    def test_retries_on_transient_error(self):
        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise OSError(errno.EIO, "I/O error")
            return "ok"

        result = retry_io(flaky, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert calls[0] == 3

    def test_raises_on_non_transient(self):
        def bad():
            raise OSError(errno.ENOENT, "No such file")

        with pytest.raises(OSError, match="No such file"):
            retry_io(bad, max_retries=3, base_delay=0.01)

    def test_raises_after_max_retries(self):
        def always_fail():
            raise OSError(errno.EIO, "I/O error")

        with pytest.raises(OSError, match="I/O error"):
            retry_io(always_fail, max_retries=2, base_delay=0.01)

    def test_passes_args_and_kwargs(self):
        def adder(a, b, offset=0):
            return a + b + offset

        result = retry_io(adder, 3, 4, offset=10, base_delay=0.01)
        assert result == 17


class TestCheckStorageAccessible:
    def test_accessible_directory(self, tmp_path):
        result = check_storage_accessible(str(tmp_path))
        assert result["accessible"] is True
        assert result["error"] is None
        assert result["latency_ms"] >= 0

    def test_nonexistent_directory(self):
        result = check_storage_accessible("/nonexistent/path/12345")
        assert result["accessible"] is False
        assert result["error"] is not None
        assert result["latency_ms"] >= 0

    def test_permission_error(self, tmp_path):
        with patch("os.listdir", side_effect=PermissionError("Permission denied")):
            result = check_storage_accessible(str(tmp_path))
            assert result["accessible"] is False


class TestSqliteBusyTimeout:
    """Verify SQLite connections have the expected NAS-friendly timeouts."""

    def test_busy_timeout_set(self, tmp_path):
        from bpp.db.connection import init_db

        conn = init_db(str(tmp_path / "test.db"))
        # PRAGMA busy_timeout returns the current value in milliseconds
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row[0] == 30000
        conn.close()


class TestRecheckMissingEndpoint:
    """Test the /api/photos/recheck-missing endpoint."""

    @pytest.fixture()
    def app_with_missing(self, tmp_path):
        """Create a Flask app with a photo marked as missing."""
        from bpp.db.photos import upsert_photo
        from bpp.web.app import create_app

        # Create the app first so it initializes the DB
        real_file = tmp_path / "real.jpg"
        real_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        missing_file = tmp_path / "gone.jpg"

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        # Seed data via the app's own connection
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            upsert_photo(conn, {"filepath": str(real_file)})
            upsert_photo(conn, {"filepath": str(missing_file)})
            conn.execute(
                "UPDATE photos SET missing=1 WHERE filepath=?",
                (str(missing_file),),
            )
            conn.commit()

        return app, str(missing_file), str(real_file)

    def test_recheck_restores_reappeared_file(self, app_with_missing, tmp_path):
        app, _missing_path, _real_path = app_with_missing

        # Create the "missing" file so it reappears
        missing = tmp_path / "gone.jpg"
        missing.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["restored"] == 1
            assert data["still_missing"] == 0

    def test_recheck_with_still_missing(self, app_with_missing):
        app, _missing_path, _real_path = app_with_missing
        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["restored"] == 0
            assert data["still_missing"] == 1

    def test_recheck_no_missing_photos(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.web.app import create_app

        db_path = str(tmp_path / "photopicker.db")
        init_db(db_path)
        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["restored"] == 0
            assert data["still_missing"] == 0


class TestStorageHealthEndpoint:
    def test_health_check_accessible(self, tmp_path):
        from bpp.db.connection import init_db
        from bpp.web.app import create_app

        db_path = str(tmp_path / "photopicker.db")
        init_db(db_path)
        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/v1/health/storage")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["accessible"] is True
            assert data["latency_ms"] >= 0
