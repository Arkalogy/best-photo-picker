"""Shared fixtures and helpers of the test_coverage_gaps.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import os

import pytest
from PIL import Image


def _real(p):
    return os.path.realpath(str(p))


@pytest.fixture()
def _suppress_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture()
def bare_app(tmp_path, _suppress_config):
    from bpp.web.app import create_app

    d = _real(tmp_path)
    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True
    return app, d


@pytest.fixture()
def app_with_photos(tmp_path, _suppress_config):
    """App seeded with photos that have dates for calendar/memories tests."""
    d = _real(tmp_path)
    for i in range(5):
        p = os.path.join(d, f"photo_{i}.jpg")
        Image.new("RGB", (100, 100), (50 + i * 40, 100, 150)).save(p, "JPEG")

    from bpp.web.app import create_app

    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.db.albums import sync_all_photos_album
        from bpp.db.photos import upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        for i in range(5):
            upsert_photo(
                conn,
                {
                    "filepath": os.path.join(d, f"photo_{i}.jpg"),
                    "aggregate_score": 0.5 + i * 0.1,
                    "blur_score": 0.7,
                    "exposure_score": 0.8,
                    "face_score": 0.3,
                    "composition_score": 0.6,
                    "date": f"2024-06-{15 + i:02d}T12:00:00",
                    "date_day": f"2024-06-{15 + i:02d}",
                    "date_month": "2024-06",
                    "sha256": f"abc{i:04d}",
                },
            )
        sync_all_photos_album(conn)
        ctx.invalidate_analysis()

    return app, d
