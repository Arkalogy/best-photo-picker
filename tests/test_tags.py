"""TDD tests for P0-4: Tags / Keywords — DB layer, API, and integration."""

from __future__ import annotations

import os
import tempfile

import pytest
from PIL import Image

# ── Helpers ──


def _make_photo(tmp_path, name="photo.jpg"):
    """Create a minimal JPEG and return its path."""
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


# ── DB: tags CRUD ──


class TestTagsCRUD:
    """Tests for tag creation, listing, renaming, and deletion."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_create_tag(self, db):
        from bpp.db.tags import create_tag

        tag_id = create_tag(db, "vacation")
        assert isinstance(tag_id, int)
        assert tag_id > 0

    def test_create_tag_returns_existing(self, db):
        from bpp.db.tags import create_tag

        id1 = create_tag(db, "vacation")
        id2 = create_tag(db, "vacation")
        assert id1 == id2

    def test_create_tag_case_insensitive(self, db):
        from bpp.db.tags import create_tag

        id1 = create_tag(db, "Vacation")
        id2 = create_tag(db, "vacation")
        assert id1 == id2

    def test_list_tags_empty(self, db):
        from bpp.db.tags import list_tags

        assert list_tags(db) == []

    def test_list_tags(self, db):
        from bpp.db.tags import create_tag, list_tags

        create_tag(db, "vacation")
        create_tag(db, "family")
        tags = list_tags(db)
        assert len(tags) == 2
        names = [t["name"] for t in tags]
        assert "vacation" in names
        assert "family" in names

    def test_rename_tag(self, db):
        from bpp.db.tags import create_tag, get_tag, rename_tag

        tag_id = create_tag(db, "vaccation")
        rename_tag(db, tag_id, "vacation")
        tag = get_tag(db, tag_id)
        assert tag["name"] == "vacation"

    def test_delete_tag(self, db):
        from bpp.db.tags import create_tag, delete_tag, list_tags

        tag_id = create_tag(db, "temp")
        delete_tag(db, tag_id)
        assert list_tags(db) == []

    def test_search_tags(self, db):
        from bpp.db.tags import create_tag, search_tags

        create_tag(db, "vacation")
        create_tag(db, "valentine")
        create_tag(db, "family")
        results = search_tags(db, "vac")
        assert len(results) == 1
        assert results[0]["name"] == "vacation"

    def test_search_tags_case_insensitive(self, db):
        from bpp.db.tags import create_tag, search_tags

        create_tag(db, "Vacation")
        results = search_tags(db, "vac")
        assert len(results) == 1


# ── DB: photo-tag associations ──


class TestPhotoTags:
    """Tests for adding/removing tags on photos."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def _add_photo(self, db, tmp_path, name="photo.jpg"):
        from bpp.db.photos import upsert_photo

        path = _make_photo(tmp_path, name)
        return upsert_photo(db, {"filepath": path}), path

    def test_add_tag_to_photo(self, db, tmp_path):
        from bpp.db.tags import add_tag_to_photo, create_tag, get_photo_tags

        photo_id, _ = self._add_photo(db, tmp_path)
        tag_id = create_tag(db, "vacation")
        add_tag_to_photo(db, photo_id, tag_id)
        tags = get_photo_tags(db, photo_id)
        assert len(tags) == 1
        assert tags[0]["name"] == "vacation"

    def test_add_tag_idempotent(self, db, tmp_path):
        from bpp.db.tags import add_tag_to_photo, create_tag, get_photo_tags

        photo_id, _ = self._add_photo(db, tmp_path)
        tag_id = create_tag(db, "vacation")
        add_tag_to_photo(db, photo_id, tag_id)
        add_tag_to_photo(db, photo_id, tag_id)
        tags = get_photo_tags(db, photo_id)
        assert len(tags) == 1

    def test_remove_tag_from_photo(self, db, tmp_path):
        from bpp.db.tags import (
            add_tag_to_photo,
            create_tag,
            get_photo_tags,
            remove_tag_from_photo,
        )

        photo_id, _ = self._add_photo(db, tmp_path)
        tag_id = create_tag(db, "vacation")
        add_tag_to_photo(db, photo_id, tag_id)
        remove_tag_from_photo(db, photo_id, tag_id)
        assert get_photo_tags(db, photo_id) == []

    def test_multiple_tags_on_photo(self, db, tmp_path):
        from bpp.db.tags import add_tag_to_photo, create_tag, get_photo_tags

        photo_id, _ = self._add_photo(db, tmp_path)
        for name in ["vacation", "family", "beach"]:
            tag_id = create_tag(db, name)
            add_tag_to_photo(db, photo_id, tag_id)
        tags = get_photo_tags(db, photo_id)
        assert len(tags) == 3

    def test_get_photos_by_tag(self, db, tmp_path):
        from bpp.db.tags import (
            add_tag_to_photo,
            create_tag,
            get_photos_by_tag,
        )

        id1, _ = self._add_photo(db, tmp_path, "a.jpg")
        self._add_photo(db, tmp_path, "b.jpg")
        tag_id = create_tag(db, "vacation")
        add_tag_to_photo(db, id1, tag_id)
        photos = get_photos_by_tag(db, tag_id)
        assert len(photos) == 1
        assert photos[0]["id"] == id1

    def test_get_photos_by_tag_excludes_deleted(self, db, tmp_path):
        from bpp.db.tags import (
            add_tag_to_photo,
            create_tag,
            get_photos_by_tag,
        )

        id1, _ = self._add_photo(db, tmp_path, "a.jpg")
        tag_id = create_tag(db, "vacation")
        add_tag_to_photo(db, id1, tag_id)
        db.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (id1,))
        db.commit()
        assert get_photos_by_tag(db, tag_id) == []

    def test_delete_tag_cascades(self, db, tmp_path):
        from bpp.db.tags import (
            add_tag_to_photo,
            create_tag,
            delete_tag,
            get_photo_tags,
        )

        photo_id, _ = self._add_photo(db, tmp_path)
        tag_id = create_tag(db, "temp")
        add_tag_to_photo(db, photo_id, tag_id)
        delete_tag(db, tag_id)
        assert get_photo_tags(db, photo_id) == []

    def test_bulk_tag_photos(self, db, tmp_path):
        from bpp.db.tags import bulk_tag_photos, create_tag, get_photo_tags

        id1, _ = self._add_photo(db, tmp_path, "a.jpg")
        id2, _ = self._add_photo(db, tmp_path, "b.jpg")
        tag_id = create_tag(db, "vacation")
        bulk_tag_photos(db, [id1, id2], tag_id)
        assert len(get_photo_tags(db, id1)) == 1
        assert len(get_photo_tags(db, id2)) == 1

    def test_bulk_untag_photos(self, db, tmp_path):
        from bpp.db.tags import (
            bulk_tag_photos,
            bulk_untag_photos,
            create_tag,
            get_photo_tags,
        )

        id1, _ = self._add_photo(db, tmp_path, "a.jpg")
        id2, _ = self._add_photo(db, tmp_path, "b.jpg")
        tag_id = create_tag(db, "vacation")
        bulk_tag_photos(db, [id1, id2], tag_id)
        bulk_untag_photos(db, [id1], tag_id)
        assert get_photo_tags(db, id1) == []
        assert len(get_photo_tags(db, id2)) == 1

    def test_list_tags_with_counts(self, db, tmp_path):
        from bpp.db.tags import (
            add_tag_to_photo,
            create_tag,
            list_tags_with_counts,
        )

        id1, _ = self._add_photo(db, tmp_path, "a.jpg")
        id2, _ = self._add_photo(db, tmp_path, "b.jpg")
        vac = create_tag(db, "vacation")
        fam = create_tag(db, "family")
        add_tag_to_photo(db, id1, vac)
        add_tag_to_photo(db, id2, vac)
        add_tag_to_photo(db, id1, fam)
        tags = list_tags_with_counts(db)
        vac_entry = next(t for t in tags if t["name"] == "vacation")
        fam_entry = next(t for t in tags if t["name"] == "family")
        assert vac_entry["count"] == 2
        assert fam_entry["count"] == 1


# ── API: /api/tags ──


class TestTagsAPI:
    """Tests for tags API endpoints."""

    @pytest.fixture()
    def app(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def _add_photo(self, app):
        """Add a photo to the DB and return its ID."""
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                Image.new("RGB", (10, 10)).save(f, "JPEG")
                path = f.name
            photo_id = upsert_photo(conn, {"filepath": path})
            return photo_id, path

    def test_list_tags_empty(self, client):
        resp = client.get("/api/v1/tags")
        assert resp.status_code == 200
        assert resp.get_json()["tags"] == []

    def test_create_tag(self, client):
        resp = client.post("/api/v1/tags", json={"name": "vacation"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] > 0
        assert data["name"] == "vacation"

    def test_create_tag_missing_name(self, client):
        resp = client.post("/api/v1/tags", json={})
        assert resp.status_code == 400

    def test_delete_tag(self, client):
        resp = client.post("/api/v1/tags", json={"name": "temp"})
        tag_id = resp.get_json()["id"]
        resp = client.delete(f"/api/v1/tags/{tag_id}")
        assert resp.status_code == 200
        resp = client.get("/api/v1/tags")
        assert resp.get_json()["tags"] == []

    def test_rename_tag(self, client):
        resp = client.post("/api/v1/tags", json={"name": "vaccation"})
        tag_id = resp.get_json()["id"]
        resp = client.put(f"/api/v1/tags/{tag_id}", json={"name": "vacation"})
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "vacation"

    def test_search_tags(self, client):
        client.post("/api/v1/tags", json={"name": "vacation"})
        client.post("/api/v1/tags", json={"name": "family"})
        resp = client.get("/api/v1/tags/search?q=vac")
        assert resp.status_code == 200
        tags = resp.get_json()["tags"]
        assert len(tags) == 1
        assert tags[0]["name"] == "vacation"

    def test_tag_photo(self, client, app):
        photo_id, path = self._add_photo(app)
        resp = client.post("/api/v1/tags", json={"name": "vacation"})
        tag_id = resp.get_json()["id"]
        resp = client.post(f"/api/v1/photos/{photo_id}/tags", json={"tag_id": tag_id})
        assert resp.status_code == 200
        resp = client.get(f"/api/v1/photos/{photo_id}/tags")
        tags = resp.get_json()["tags"]
        assert len(tags) == 1
        assert tags[0]["name"] == "vacation"
        os.unlink(path)

    def test_untag_photo(self, client, app):
        photo_id, path = self._add_photo(app)
        resp = client.post("/api/v1/tags", json={"name": "vacation"})
        tag_id = resp.get_json()["id"]
        client.post(f"/api/v1/photos/{photo_id}/tags", json={"tag_id": tag_id})
        resp = client.delete(f"/api/v1/photos/{photo_id}/tags/{tag_id}")
        assert resp.status_code == 200
        resp = client.get(f"/api/v1/photos/{photo_id}/tags")
        assert resp.get_json()["tags"] == []
        os.unlink(path)

    def test_tag_photo_by_name(self, client, app):
        """Tag a photo by tag name — creates the tag if it doesn't exist."""
        photo_id, path = self._add_photo(app)
        resp = client.post(f"/api/v1/photos/{photo_id}/tags", json={"name": "newTag"})
        assert resp.status_code == 200
        resp = client.get(f"/api/v1/photos/{photo_id}/tags")
        tags = resp.get_json()["tags"]
        assert len(tags) == 1
        assert tags[0]["name"] == "newtag"  # normalized to lowercase
        os.unlink(path)

    def test_batch_tag(self, client, app):
        id1, p1 = self._add_photo(app)
        id2, p2 = self._add_photo(app)
        resp = client.post("/api/v1/tags", json={"name": "vacation"})
        tag_id = resp.get_json()["id"]
        resp = client.post(
            "/api/v1/tags/batch",
            json={"photo_ids": [id1, id2], "tag_id": tag_id},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 2
        os.unlink(p1)
        os.unlink(p2)

    def test_batch_untag(self, client, app):
        id1, p1 = self._add_photo(app)
        id2, p2 = self._add_photo(app)
        resp = client.post("/api/v1/tags", json={"name": "vacation"})
        tag_id = resp.get_json()["id"]
        client.post(
            "/api/v1/tags/batch",
            json={"photo_ids": [id1, id2], "tag_id": tag_id},
        )
        resp = client.post(
            "/api/v1/tags/batch/remove",
            json={"photo_ids": [id1], "tag_id": tag_id},
        )
        assert resp.status_code == 200
        os.unlink(p1)
        os.unlink(p2)


class TestMergeTags:
    """merge_tags: links move, duplicates collapse, source dies."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def _photo(self, db, fp, score=0.5):
        db.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, "
            "file_mtime, aggregate_score) VALUES (?, ?, 1, 1.0, ?)",
            (fp, fp.split("/")[-1], score),
        )
        db.commit()
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_merge_moves_links_and_deletes_source(self, db):
        from bpp.db.tags import add_tag_to_photo, create_tag, get_photo_tags, get_tag, merge_tags

        a = create_tag(db, "bday")
        b = create_tag(db, "birthday")
        p1 = self._photo(db, "/x/1.jpg")
        p2 = self._photo(db, "/x/2.jpg")
        add_tag_to_photo(db, p1, a)
        add_tag_to_photo(db, p2, a)
        add_tag_to_photo(db, p2, b)  # overlap — must collapse, not dupe

        moved = merge_tags(db, a, b)
        assert moved >= 1
        assert get_tag(db, a) is None, "source tag must be deleted"
        for pid in (p1, p2):
            names = [t["name"] for t in get_photo_tags(db, pid)]
            assert names == ["birthday"], f"photo {pid}: {names}"

    def test_merge_into_self_is_noop(self, db):
        from bpp.db.tags import add_tag_to_photo, create_tag, get_photo_tags, get_tag, merge_tags

        a = create_tag(db, "solo")
        p = self._photo(db, "/x/3.jpg")
        add_tag_to_photo(db, p, a)
        assert merge_tags(db, a, a) == 0
        assert get_tag(db, a) is not None
        assert [t["name"] for t in get_photo_tags(db, p)] == ["solo"]

    def test_list_with_counts_includes_top_scored_cover(self, db):
        from bpp.db.tags import add_tag_to_photo, create_tag, list_tags_with_counts

        t = create_tag(db, "cover")
        lo = self._photo(db, "/x/lo.jpg", score=0.2)
        hi = self._photo(db, "/x/hi.jpg", score=0.9)
        add_tag_to_photo(db, lo, t)
        add_tag_to_photo(db, hi, t)
        row = next(r for r in list_tags_with_counts(db) if r["name"] == "cover")
        assert row["count"] == 2
        assert row["cover_filepath"] == "/x/hi.jpg", "cover must be the top-scored member"
