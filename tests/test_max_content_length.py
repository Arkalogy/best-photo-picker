"""TDD tests for H-9: MAX_CONTENT_LENGTH must be set."""

from __future__ import annotations

import os

from bpp.web.app import create_app


class TestMaxContentLength:
    def test_max_content_length_is_set(self, tmp_path):
        workdir = str(tmp_path / "w")
        os.makedirs(workdir)
        app = create_app(workdir=workdir)
        assert app.config["MAX_CONTENT_LENGTH"] is not None
        # Must be reasonable: at least 1MB, at most 100MB
        assert 1 * 1024 * 1024 <= app.config["MAX_CONTENT_LENGTH"] <= 100 * 1024 * 1024

    def test_oversized_request_rejected(self, tmp_path):
        workdir = str(tmp_path / "w")
        os.makedirs(workdir)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        with app.test_client() as c:
            # Send a body larger than MAX_CONTENT_LENGTH
            limit = app.config["MAX_CONTENT_LENGTH"]
            resp = c.put(
                "/api/v1/settings",
                data=b"x" * (limit + 1),
                content_type="application/json",
            )
            assert resp.status_code == 413
