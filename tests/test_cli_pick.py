"""Tests for the `bpp pick` CLI command."""

from __future__ import annotations

import json

import pytest

from bpp.cli import build_parser, main
from bpp.db.albums import create_album
from bpp.db.connection import close_all_connections, get_db, init_db
from bpp.db.photos import upsert_photo


@pytest.fixture(autouse=True)
def _cleanup_connections():
    """Ensure thread-local DB connections are closed after each test."""
    yield
    close_all_connections()


@pytest.fixture
def library(tmp_path):
    """Create a library dir with DB and sample analyzed photos."""
    lib = tmp_path / "library"
    lib.mkdir()
    photos_dir = lib / "photos"
    photos_dir.mkdir()
    data_dir = lib / "data"
    data_dir.mkdir()

    db_path = str(data_dir / "photopicker.db")
    conn = init_db(db_path)

    # Create 10 fake analyzed photos with scores
    for i in range(10):
        img = photos_dir / f"IMG_{i:04d}.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
        upsert_photo(
            conn,
            {
                "filepath": str(img),
                "date": f"2025-06-{10 + i:02d}T12:00:00",
                "date_day": f"2025-06-{10 + i:02d}",
                "date_month": "2025-06",
                "blur_raw": 50.0 + i * 10,
                "blur_score": 0.3 + i * 0.07,
                "exposure_score": 0.5 + i * 0.05,
                "face_score": 0.2 + i * 0.08,
                "face_count": 1 if i % 2 == 0 else 0,
                "largest_face_ratio": 0.1 if i % 2 == 0 else 0.0,
                "face_center_dist": 0.3,
                "composition_score": 0.4 + i * 0.06,
                "aggregate_score": 0.3 + i * 0.07,
            },
        )

    close_all_connections()
    return lib


@pytest.fixture
def library_with_faces(library):
    """Library with face cluster data and named person albums."""
    db_path = str(library / "data" / "photopicker.db")
    conn = get_db(db_path)

    # Get photo IDs
    rows = conn.execute("SELECT id, filepath FROM photos ORDER BY id").fetchall()
    photo_ids = [(r[0], r[1]) for r in rows]

    # Create face_embeddings for some photos
    # cluster 0 = "Alex", cluster 1 = "Sam"
    import numpy as np

    for pid, _fp in photo_ids[:5]:
        embedding = np.random.default_rng(pid).random(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            "embedding, cluster_id) VALUES (?, 0, 10, 10, 50, 50, ?, 0)",
            (pid, embedding.tobytes()),
        )

    for pid, _fp in photo_ids[3:7]:
        embedding = np.random.default_rng(pid + 100).random(128).astype(np.float32)
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            "embedding, cluster_id) VALUES (?, 1, 60, 60, 40, 40, ?, 1)",
            (pid, embedding.tobytes()),
        )
    conn.commit()

    # Create smart_person albums with names
    create_album(conn, name="Alex", album_type="smart_person", rule={"cluster_id": 0})
    create_album(conn, name="Sam", album_type="smart_person", rule={"cluster_id": 1})

    close_all_connections()
    return library


# --- Parser tests ---


class TestPickParser:
    def test_pick_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/some/path", "--top", "20"])
        assert args.command == "pick"
        assert args.library == "/some/path"
        assert args.top == 20

    def test_pick_default_top(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/some/path"])
        assert args.top == 50

    def test_pick_k_alias(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/some/path", "-k", "30"])
        assert args.top == 30

    def test_pick_boost_face_single(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/some/path", "--boost-face", "Alex"])
        assert args.boost_face == ["Alex"]

    def test_pick_boost_face_multiple(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "pick",
                "/some/path",
                "--boost-face",
                "Alex",
                "--boost-face",
                "Sam",
            ]
        )
        assert args.boost_face == ["Alex", "Sam"]

    def test_pick_out_dir(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/lib", "--out", "/tmp/out"])
        assert args.out == "/tmp/out"

    def test_pick_json_flag(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/lib", "--json"])
        assert args.json is True

    def test_pick_paths_only_flag(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/lib", "--paths-only"])
        assert args.paths_only is True

    def test_pick_quality_choices(self):
        parser = build_parser()
        for q in ("original", "high", "medium", "low"):
            args = parser.parse_args(["pick", "/lib", "--quality", q])
            assert args.quality == q

    def test_pick_quality_default(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/lib"])
        assert args.quality == "original"

    def test_pick_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["pick", "/lib", "--dry-run"])
        assert args.dry_run is True


# --- Command behavior tests ---


class TestPickCommand:
    def test_pick_no_analysis_errors(self, tmp_path):
        """pick on an empty library (no photos) should error."""
        lib = tmp_path / "empty_lib"
        lib.mkdir()
        data_dir = lib / "data"
        data_dir.mkdir()
        db_path = str(data_dir / "photopicker.db")
        init_db(db_path)
        close_all_connections()

        rc = main(["pick", str(lib)])
        assert rc != 0

    def test_pick_resolves_canonical_db_path(self, tmp_path):
        """Regression for round-9: `bpp pick` must look for the DB at
        `<library>/data/photopicker.db` to match the layout that
        `bpp serve` (and every other write path) uses. A pre-fix run
        looked for `<library>/photopicker.db` and erroneously errored
        out on every library produced by the standard pipeline."""
        lib = tmp_path / "lib_canonical"
        lib.mkdir()
        # Writing the DB at the legacy (wrong) path must NOT satisfy pick.
        legacy_path = lib / "photopicker.db"
        init_db(str(legacy_path))
        close_all_connections()

        rc = main(["pick", str(lib)])
        # Exits with the "no database found" error because the canonical
        # location (lib/data/photopicker.db) does not exist.
        assert rc != 0

    def test_pick_default_output(self, library, capsys):
        """Default output should be a human-readable table."""
        rc = main(["pick", str(library), "--top", "3"])
        assert rc == 0
        captured = capsys.readouterr()
        # Should show some kind of table header and rows
        assert "score" in captured.out.lower() or "Score" in captured.out
        lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
        # Header + separator + at least 1 data row
        assert len(lines) >= 2

    def test_pick_paths_only(self, library, capsys):
        """--paths-only should output one filepath per line."""
        rc = main(["pick", str(library), "--top", "3", "--paths-only"])
        assert rc == 0
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
        assert len(lines) == 3
        for line in lines:
            assert line.endswith(".jpg")

    def test_pick_json_output(self, library, capsys):
        """--json should output valid JSON with expected fields."""
        rc = main(["pick", str(library), "--top", "3", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 3
        for item in data:
            assert "filepath" in item
            assert "aggregate_score" in item
            assert "blur_score" in item
            assert "exposure_score" in item
            assert "face_score" in item
            assert "composition_score" in item

    def test_pick_top_limits_selection(self, library, capsys):
        """--top N should return exactly N photos."""
        rc = main(["pick", str(library), "--top", "5", "--paths-only"])
        assert rc == 0
        lines = [ln for ln in capsys.readouterr().out.strip().split("\n") if ln.strip()]
        assert len(lines) == 5

    def test_pick_top_exceeds_available(self, library, capsys):
        """If --top > available photos, return all available."""
        rc = main(["pick", str(library), "--top", "100", "--paths-only"])
        assert rc == 0
        lines = [ln for ln in capsys.readouterr().out.strip().split("\n") if ln.strip()]
        assert len(lines) == 10  # only 10 photos in library

    def test_pick_dry_run_no_export(self, library, tmp_path, capsys):
        """--dry-run with --out should not create the output directory."""
        outdir = tmp_path / "exported"
        rc = main(
            [
                "pick",
                str(library),
                "--top",
                "3",
                "--out",
                str(outdir),
                "--dry-run",
            ]
        )
        assert rc == 0
        assert not outdir.exists()

    def test_pick_export(self, library, tmp_path):
        """--out should export selected photos to the directory."""
        outdir = tmp_path / "exported"
        rc = main(
            [
                "pick",
                str(library),
                "--top",
                "3",
                "--out",
                str(outdir),
            ]
        )
        assert rc == 0
        assert outdir.exists()
        # Export creates a "selected" subdir
        selected_dir = outdir / "selected"
        assert selected_dir.exists()
        exported = list(selected_dir.iterdir())
        assert len(exported) == 3

    def test_pick_export_quality_medium(self, library, tmp_path):
        """--quality medium should pass through to export."""
        outdir = tmp_path / "exported_med"
        rc = main(
            [
                "pick",
                str(library),
                "--top",
                "2",
                "--out",
                str(outdir),
                "--quality",
                "medium",
            ]
        )
        assert rc == 0
        assert outdir.exists()

    def test_pick_nonexistent_library(self, tmp_path):
        """pick on a non-existent directory should error."""
        rc = main(["pick", str(tmp_path / "nope")])
        assert rc != 0


# --- Face boost tests ---


class TestPickFaceBoost:
    def test_boost_face_by_name(self, library_with_faces, capsys):
        """--boost-face Alex should succeed and boost Alex's photos."""
        rc = main(
            [
                "pick",
                str(library_with_faces),
                "--top",
                "5",
                "--boost-face",
                "Alex",
                "--paths-only",
            ]
        )
        assert rc == 0
        lines = [ln for ln in capsys.readouterr().out.strip().split("\n") if ln.strip()]
        assert len(lines) == 5

    def test_boost_face_unknown_name_errors(self, library_with_faces):
        """--boost-face with an unknown name should error."""
        rc = main(
            [
                "pick",
                str(library_with_faces),
                "--top",
                "5",
                "--boost-face",
                "Unknown",
            ]
        )
        assert rc != 0

    def test_boost_multiple_faces(self, library_with_faces, capsys):
        """Multiple --boost-face flags should work."""
        rc = main(
            [
                "pick",
                str(library_with_faces),
                "--top",
                "5",
                "--boost-face",
                "Alex",
                "--boost-face",
                "Sam",
                "--json",
            ]
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 5


# --- Edge cases ---


class TestPickEdgeCases:
    def test_pick_json_and_paths_only_conflict(self):
        """--json and --paths-only should be mutually exclusive."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["pick", "/lib", "--json", "--paths-only"])
