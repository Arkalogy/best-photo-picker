"""Shared fixtures and helpers of the test_smart_album_coverage.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from bpp.db.connection import get_db, init_db
from bpp.db.photos import upsert_photo


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database and return its path."""
    path = str(tmp_path / "smarttest.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    """Return a connection to the test database."""
    return get_db(db_path)


def _make_photo(conn, tmp_path, name, **extra):
    """Create a dummy file on disk and upsert it into the photos table."""
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    photo = {"filepath": str(f)}
    photo.update(extra)
    return upsert_photo(conn, photo)


def _add_face(conn, photo_id, face_index, cluster_id):
    """Insert a face embedding row for a given photo and cluster."""
    embedding = np.zeros(128, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT OR REPLACE INTO face_embeddings "
        "(photo_id, face_index, embedding, cluster_id) VALUES (?, ?, ?, ?)",
        (photo_id, face_index, embedding, cluster_id),
    )
    conn.commit()


def _add_pet(conn, photo_id, pet_class, cluster_id, detection_index=0):
    """Insert a pet detection row for a given photo and cluster."""
    conn.execute(
        "INSERT INTO pet_detections "
        "(photo_id, detection_index, class, confidence, cluster_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (photo_id, detection_index, pet_class, 0.9, cluster_id),
    )
    conn.commit()


def _make_jpeg(path):
    """Write a tiny valid JPEG via PIL so thumbnail generation can succeed."""
    img = Image.new("RGB", (100, 100), "red")
    img.save(str(path), "JPEG")
