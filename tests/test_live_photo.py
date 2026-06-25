"""Tests for Live Photo sidecar detection and filtering.

Covers the assumptions documented in bpp/db/live_photo.py:
  - Single-digit _N suffix → sidecar when parent exists in same dir
  - Double-digit suffix → NOT a sidecar (sequential shots)
  - Parent missing → import as regular photo (no silent drop)
  - Parent in different directory → NOT a sidecar
  - filter_sidecar_paths only drops sidecars when parent is in same batch
"""

from __future__ import annotations

import pytest


class TestIsLivePhotoSidecarFilename:
    from bpp.db.live_photo import is_live_photo_sidecar_filename

    def test_standard_heic_sidecar(self):
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        result = is_live_photo_sidecar_filename("IMG_4214_1.HEIC")
        assert result == ("IMG_4214.HEIC", ".HEIC")

    def test_standard_jpg_sidecar(self):
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        result = is_live_photo_sidecar_filename("DSC_0012_1.JPG")
        assert result == ("DSC_0012.JPG", ".JPG")

    def test_n2_sidecar(self):
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        result = is_live_photo_sidecar_filename("IMG_4214_2.HEIC")
        assert result == ("IMG_4214.HEIC", ".HEIC")

    def test_double_digit_not_sidecar(self):
        """photo_12.jpg has a two-digit suffix — sequential shot, not sidecar."""
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        assert is_live_photo_sidecar_filename("photo_12.jpg") is None

    def test_no_underscore_not_sidecar(self):
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        assert is_live_photo_sidecar_filename("IMG_4214.HEIC") is None

    def test_case_insensitive(self):
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        result = is_live_photo_sidecar_filename("img_4214_1.heic")
        assert result is not None
        assert result[0].lower() == "img_4214.heic"

    def test_filename_with_parens(self):
        """IMG_5193(1)_1.JPG — filename with parens, single-digit suffix."""
        from bpp.db.live_photo import is_live_photo_sidecar_filename

        result = is_live_photo_sidecar_filename("IMG_5193(1)_1.JPG")
        assert result == ("IMG_5193(1).JPG", ".JPG")


class TestFilterSidecarPaths:
    def test_filters_sidecar_when_parent_present(self):
        from bpp.db.live_photo import filter_sidecar_paths

        paths = ["/photos/IMG_4214.HEIC", "/photos/IMG_4214_1.HEIC"]
        keep, skipped = filter_sidecar_paths(paths)
        assert "/photos/IMG_4214.HEIC" in keep
        assert "/photos/IMG_4214_1.HEIC" in skipped

    def test_keeps_sidecar_when_parent_absent(self):
        """If parent not in batch, keep the sidecar — never silently drop."""
        from bpp.db.live_photo import filter_sidecar_paths

        paths = ["/photos/IMG_4214_1.HEIC"]  # no parent
        keep, skipped = filter_sidecar_paths(paths)
        assert "/photos/IMG_4214_1.HEIC" in keep
        assert not skipped

    def test_different_directory_not_filtered(self):
        """Sidecar pattern match but parent in a different directory → keep both."""
        from bpp.db.live_photo import filter_sidecar_paths

        paths = ["/dir_a/IMG_4214.HEIC", "/dir_b/IMG_4214_1.HEIC"]
        keep, skipped = filter_sidecar_paths(paths)
        assert len(keep) == 2
        assert not skipped

    def test_multiple_sidecars(self):
        from bpp.db.live_photo import filter_sidecar_paths

        paths = [
            "/p/A.HEIC",
            "/p/A_1.HEIC",
            "/p/B.JPG",
            "/p/B_1.JPG",
            "/p/C.HEIC",  # no sidecar
        ]
        keep, skipped = filter_sidecar_paths(paths)
        assert set(keep) == {"/p/A.HEIC", "/p/B.JPG", "/p/C.HEIC"}
        assert set(skipped) == {"/p/A_1.HEIC", "/p/B_1.JPG"}


class TestSplitScanForConfirmedSidecars:
    """The analyze-path perf pre-pass: pull phash-confirmed sidecars out of
    scoring, never on filename alone."""

    def _touch(self, tmp_path, name):
        p = tmp_path / name
        p.write_bytes(b"x")
        return str(p)

    def test_confirmed_sidecar_pulled_from_scoring(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_4214.HEIC")
        sidecar = self._touch(tmp_path, "IMG_4214_1.HEIC")
        # Identical hashes → confirmed duplicate.
        hashes = {parent: (0xABCD, 0x12), sidecar: (0xABCD, 0x12)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, sidecar], compute_hashes=lambda p: hashes[p]
        )

        assert to_score == [parent], "parent stays in scoring; sidecar removed"
        assert len(records) == 1
        rec = records[0]
        assert rec["filepath"] == sidecar
        assert rec["original_filename"] == "IMG_4214_1.HEIC"
        assert rec["phash"] == 0xABCD
        assert rec["ahash"] == 0x12
        assert rec["file_size"] == 1
        assert "file_mtime" in rec

    def test_divergent_hash_stays_in_scoring(self, tmp_path):
        """A '_N' file whose phash diverges from its parent is a distinct
        photo (beach_2.jpg next to beach.jpg) — it must be scored, not
        dropped on filename alone."""
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "beach.jpg")
        distinct = self._touch(tmp_path, "beach_2.jpg")
        hashes = {parent: (0x0, 0x0), distinct: (0x7FFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, distinct], compute_hashes=lambda p: hashes[p]
        )

        assert set(to_score) == {parent, distinct}
        assert records == []

    def test_missing_hash_stays_in_scoring(self, tmp_path):
        """If the parent can't be hashed, can't confirm → keep visible, and
        don't waste a decode on the candidate (P-05)."""
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_1.HEIC")
        sidecar = self._touch(tmp_path, "IMG_1_1.HEIC")
        hashes = {parent: (None, None), sidecar: (0xABCD, 0x12)}
        decoded = []

        def _spy(p):
            decoded.append(p)
            return hashes[p]

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, sidecar], compute_hashes=_spy
        )

        assert set(to_score) == {parent, sidecar}
        assert records == []
        assert sidecar not in decoded, "candidate must not be decoded when parent is unhashable"

    def test_one_hash_match_not_enough(self, tmp_path):
        """dHash identical but aHash far apart → not a duplicate, stays in
        scoring. Strict both-hashes guard (P-01)."""
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_1.HEIC")
        candidate = self._touch(tmp_path, "IMG_1_1.HEIC")
        hashes = {parent: (0x00, 0x00), candidate: (0x00, 0x7FFFFFFFFFFFFFFF)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, candidate], compute_hashes=lambda p: hashes[p]
        )

        assert set(to_score) == {parent, candidate}
        assert records == []

    def test_parent_not_in_batch_stays_in_scoring(self, tmp_path):
        """'_N' file with no parent in the batch is never touched — no decode,
        no drop."""
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        orphan = self._touch(tmp_path, "IMG_4214_1.HEIC")
        decoded = []

        def _spy(p):
            decoded.append(p)
            return (0xABCD, 0x12)

        to_score, records = split_scan_for_confirmed_sidecars([orphan], compute_hashes=_spy)

        assert to_score == [orphan]
        assert records == []
        assert decoded == [], "no parent → no decode at all"

    def test_different_directory_not_confirmed(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        da = tmp_path / "a"
        db = tmp_path / "b"
        da.mkdir()
        db.mkdir()
        parent = self._touch(da, "IMG_4214.HEIC")
        sidecar = self._touch(db, "IMG_4214_1.HEIC")
        hashes = {parent: (0xABCD, 0x12), sidecar: (0xABCD, 0x12)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, sidecar], compute_hashes=lambda p: hashes[p]
        )

        assert set(to_score) == {parent, sidecar}
        assert records == []

    def test_mixed_confirmed_and_distinct(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        p1 = self._touch(tmp_path, "A.HEIC")
        s1 = self._touch(tmp_path, "A_1.HEIC")  # confirmed dup of A
        p2 = self._touch(tmp_path, "B.jpg")
        d2 = self._touch(tmp_path, "B_2.jpg")  # distinct
        hashes = {
            p1: (0x10, 0x10),
            s1: (0x10, 0x10),
            p2: (0x0, 0x0),
            d2: (0x7FFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF),
        }

        to_score, records = split_scan_for_confirmed_sidecars(
            [p1, s1, p2, d2], compute_hashes=lambda p: hashes[p]
        )

        assert set(to_score) == {p1, p2, d2}
        assert [r["filepath"] for r in records] == [s1]

    def test_parent_decoded_once_for_multiple_sidecars(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_4214.HEIC")
        s1 = self._touch(tmp_path, "IMG_4214_1.HEIC")
        s2 = self._touch(tmp_path, "IMG_4214_2.HEIC")
        counts = {}

        def _spy(p):
            counts[p] = counts.get(p, 0) + 1
            return (0xABCD, 0x12)

        to_score, records = split_scan_for_confirmed_sidecars([parent, s1, s2], compute_hashes=_spy)

        assert to_score == [parent]
        assert {r["filepath"] for r in records} == {s1, s2}
        assert counts[parent] == 1, "parent decoded once across both sidecars"

    def test_on_progress_ticks_per_parent(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_1.HEIC")
        sidecar = self._touch(tmp_path, "IMG_1_1.HEIC")
        hashes = {parent: (0x1, 0x1), sidecar: (0x1, 0x1)}
        ticks = []

        split_scan_for_confirmed_sidecars(
            [parent, sidecar],
            compute_hashes=lambda p: hashes[p],
            on_progress=lambda done, of: ticks.append((done, of)),
        )

        assert ticks == [(1, 1)]

    def test_low_confirm_rate_aborts_after_probe(self, tmp_path):
        """On a burst-style library (many '_N' files, none real duplicates),
        the pre-scan must abort after the probe slice instead of decoding the
        whole library for no payoff (P-02). Worst-case decode work is bounded
        to ~one probe slice."""
        from bpp.db import live_photo
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        probe = live_photo._SIDECAR_PROBE_SIZE
        n_pairs = probe + 100  # comfortably past one probe slice
        paths = []
        hashes = {}
        for k in range(n_pairs):
            parent = self._touch(tmp_path, f"B{k}.jpg")
            twin = self._touch(tmp_path, f"B{k}_1.jpg")  # distinct '_N'
            paths += [parent, twin]
            # Twin sits 28 bits away from its parent on BOTH hashes → never
            # confirms (0% rate), the burst-library worst case.
            hashes[parent] = (k, k)
            hashes[twin] = (k ^ 0x0FFFFFFF, k ^ 0x0FFFFFFF)
        calls = {"n": 0}

        def _spy(p):
            calls["n"] += 1
            return hashes[p]

        to_score, records = split_scan_for_confirmed_sidecars(paths, compute_hashes=_spy)

        assert records == [], "nothing confirms on a burst library"
        assert set(to_score) == set(paths), "all files still scored"
        # Without the abort this would decode all 2*n_pairs paths. The probe
        # decodes <= probe candidates + their probe parents, then stops.
        assert calls["n"] <= 2 * probe, (
            f"pre-scan decoded {calls['n']} files; early-abort should bound it "
            f"to ~one probe slice (<= {2 * probe})"
        )

    def test_high_confirm_rate_processes_all(self, tmp_path):
        """A large genuine Live-Photo library (every '_N' is a true twin) must
        NOT be aborted — all confirmed sidecars are pulled, even past the probe
        slice."""
        from bpp.db import live_photo
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        n_pairs = live_photo._SIDECAR_PROBE_SIZE + 50
        paths = []
        hashes = {}
        for k in range(n_pairs):
            parent = self._touch(tmp_path, f"L{k}.jpg")
            twin = self._touch(tmp_path, f"L{k}_1.jpg")
            paths += [parent, twin]
            hashes[parent] = (k << 4, k << 4)
            hashes[twin] = (k << 4, k << 4)  # identical → confirms

        _to_score, records = split_scan_for_confirmed_sidecars(
            paths, compute_hashes=lambda p: hashes[p]
        )

        assert len(records) == n_pairs, "every twin confirmed, none lost to early-abort"

    def test_every_record_has_parent_in_to_score(self, tmp_path):
        """LOAD-BEARING INVARIANT (P-03): every stored sidecar row must be
        accompanied by a parent that gets scored. Stored sidecars are tagged
        ONLY by the phash thread, which runs only because the parent's phash is
        NULL after scoring. If a confirmed sidecar's parent were ever absent
        from the scoring list, that row would linger as a visible score-0 ghost
        photo. This pins the property at its source: the parent of every
        returned record stays in to_score."""
        import os as _os

        from bpp.db.live_photo import (
            is_live_photo_sidecar_filename,
            split_scan_for_confirmed_sidecars,
        )

        p1 = self._touch(tmp_path, "A.HEIC")
        s1 = self._touch(tmp_path, "A_1.HEIC")  # confirmed twin of A
        p2 = self._touch(tmp_path, "B.jpg")
        d2 = self._touch(tmp_path, "B_2.jpg")  # distinct
        orphan = self._touch(tmp_path, "C_1.jpg")  # no parent in batch
        hashes = {
            p1: (0x10, 0x10),
            s1: (0x10, 0x10),
            p2: (0x0, 0x0),
            d2: (0x7FFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF),
            orphan: (0x55, 0x55),
        }

        to_score, records = split_scan_for_confirmed_sidecars(
            [p1, s1, p2, d2, orphan], compute_hashes=lambda p: hashes[p]
        )

        assert records, "fixture must produce at least one confirmed sidecar"
        for rec in records:
            parent_name, _ = is_live_photo_sidecar_filename(rec["original_filename"])
            parent_path = _os.path.join(_os.path.dirname(rec["filepath"]), parent_name)
            assert parent_path in to_score, (
                f"sidecar {rec['filepath']} stored but its parent {parent_path} "
                "is not scored — it would never get tagged (ghost row)"
            )

    def test_no_candidates_returns_input_unchanged(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        paths = [self._touch(tmp_path, "a.jpg"), self._touch(tmp_path, "b.jpg")]
        to_score, records = split_scan_for_confirmed_sidecars(
            paths, compute_hashes=lambda p: (0x0, 0x0)
        )
        assert to_score == paths
        assert records == []


class TestDetectAndLink:
    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        return conn

    def _insert_photo(self, conn, filepath, original_filename):
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
            "VALUES (?, ?, 1024, 1700000000.0)",
            (filepath, original_filename),
        )
        conn.commit()
        return conn.execute("SELECT id FROM photos WHERE filepath=?", (filepath,)).fetchone()[0]

    def test_links_sidecar_to_parent(self, db):
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        parent_id = self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        sidecar_id = self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")

        count = detect_and_link_live_photo_sidecars(db)

        assert count == 1
        row = db.execute(
            "SELECT is_live_photo_sidecar, live_photo_parent_id FROM photos WHERE id=?",
            (sidecar_id,),
        ).fetchone()
        assert row[0] == 1
        assert row[1] == parent_id

    def test_parent_not_marked_as_sidecar(self, db):
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        parent_id = self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")

        detect_and_link_live_photo_sidecars(db)

        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (parent_id,)
        ).fetchone()
        assert row[0] == 0

    def test_different_directory_not_linked(self, db):
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        self._insert_photo(db, "/dir_a/IMG_4214.HEIC", "IMG_4214.HEIC")
        sidecar_id = self._insert_photo(db, "/dir_b/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")

        count = detect_and_link_live_photo_sidecars(db)

        assert count == 0
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (sidecar_id,)
        ).fetchone()
        assert row[0] == 0

    def test_idempotent(self, db):
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")

        assert detect_and_link_live_photo_sidecars(db) == 1
        assert detect_and_link_live_photo_sidecars(db) == 0  # already linked

    def test_excluded_from_active_photo_sql(self, db):
        from bpp.constants import ACTIVE_PHOTO_SQL

        self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")

        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        detect_and_link_live_photo_sidecars(db)

        count = db.execute(f"SELECT COUNT(*) FROM photos WHERE {ACTIVE_PHOTO_SQL}").fetchone()[0]
        assert count == 1, "sidecar must be excluded from ACTIVE_PHOTO_SQL"

    def _set_hashes(self, conn, photo_id, phash, ahash):
        conn.execute("UPDATE photos SET phash=?, ahash=? WHERE id=?", (phash, ahash, photo_id))
        conn.commit()

    def test_phash_match_hides_confirmed_duplicate(self, db):
        """With require_phash_match, a '_N' file whose phash equals its
        parent's IS hidden — it's the same Live Photo still frame."""
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        parent_id = self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        sidecar_id = self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")
        self._set_hashes(db, parent_id, 0xDEADBEEF, 0x1234)
        self._set_hashes(db, sidecar_id, 0xDEADBEEF, 0x1234)  # identical

        count = detect_and_link_live_photo_sidecars(db, require_phash_match=True)

        assert count == 1
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (sidecar_id,)
        ).fetchone()
        assert row[0] == 1

    def test_phash_mismatch_keeps_distinct_photo_visible(self, db):
        """With require_phash_match, a '_N' file whose phash diverges from
        its parent is NOT hidden — e.g. a genuinely distinct beach_2.jpg
        next to beach.jpg. This is the latent-risk guard: the filename
        heuristic alone must never drop a unique photo."""
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        parent_id = self._insert_photo(db, "/lib/beach.jpg", "beach.jpg")
        distinct_id = self._insert_photo(db, "/lib/beach_2.jpg", "beach_2.jpg")
        self._set_hashes(db, parent_id, 0x0, 0x0)
        # Wildly different hash — 63 bits set → distance 63, far above the
        # 2-bit budget. (0x7FFF… stays within SQLite's signed-64-bit INTEGER.)
        self._set_hashes(db, distinct_id, 0x7FFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF)

        count = detect_and_link_live_photo_sidecars(db, require_phash_match=True)

        assert count == 0
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (distinct_id,)
        ).fetchone()
        assert row[0] == 0, "a phash-distinct '_N' photo must stay visible"

    def test_one_hash_matches_other_diverges_not_confirmed(self, db):
        """Both dHash AND aHash must be within budget to hide a photo. A '_N'
        file that matches its parent on ONE fingerprint but diverges wildly on
        the other is NOT a duplicate and must stay visible — guards against the
        lenient min()-of-two-distances direction (P-01)."""
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        parent_id = self._insert_photo(db, "/lib/IMG_5.HEIC", "IMG_5.HEIC")
        candidate_id = self._insert_photo(db, "/lib/IMG_5_1.HEIC", "IMG_5_1.HEIC")
        # dHash identical (distance 0), aHash 63 bits apart (distance 63).
        # min()-based confirmation would wrongly hide this; strict must not.
        self._set_hashes(db, parent_id, 0x00, 0x00)
        self._set_hashes(db, candidate_id, 0x00, 0x7FFFFFFFFFFFFFFF)

        count = detect_and_link_live_photo_sidecars(db, require_phash_match=True)

        assert count == 0
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (candidate_id,)
        ).fetchone()
        assert row[0] == 0, "match on only one hash must NOT confirm a sidecar"

    def test_phash_match_skips_when_hashes_missing(self, db):
        """With require_phash_match, a candidate is left visible when either
        side lacks a phash — detection defers to the phash backfill thread,
        which re-runs once hashes land. Never hide on filename alone."""
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        sidecar_id = self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")
        # No hashes set → both NULL.

        count = detect_and_link_live_photo_sidecars(db, require_phash_match=True)

        assert count == 0
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (sidecar_id,)
        ).fetchone()
        assert row[0] == 0

    def test_no_phash_match_tags_on_filename_alone(self, db):
        """Default (require_phash_match=False) preserves the import_folder
        contract: tag on filename + same-directory parent, no hash needed.
        Import filters at scan time before any hash exists."""
        from bpp.db.live_photo import detect_and_link_live_photo_sidecars

        self._insert_photo(db, "/lib/IMG_4214.HEIC", "IMG_4214.HEIC")
        sidecar_id = self._insert_photo(db, "/lib/IMG_4214_1.HEIC", "IMG_4214_1.HEIC")
        # No hashes, but require_phash_match defaults to False.

        count = detect_and_link_live_photo_sidecars(db)

        assert count == 1
        row = db.execute(
            "SELECT is_live_photo_sidecar FROM photos WHERE id=?", (sidecar_id,)
        ).fetchone()
        assert row[0] == 1


def test_phash_confirmed_sidecar_detection_runs_after_hashing():
    """Sidecar detection moved OUT of the analyze worker (where phash is
    NULL at write time) and INTO the phash backfill thread, gated on
    require_phash_match. This ordering is load-bearing: we must never hide a
    '_N' file on filename alone, and the hashes that confirm it's a true
    duplicate only exist after precompute_phashes runs. Found in the
    2026-06-08 clean-room run on Leo's library (3074 sidecars), hardened
    2026-06-09 with the phash guard.

    Wiring guard: the phash backfill thread + multiprocessing analyze worker
    are impractical to drive in a unit test. Assert (a) the detection now
    runs in state_init with require_phash_match=True, and (b) the analyze
    worker no longer calls it (phash NULL there)."""
    import inspect

    from bpp.web import analyze_worker, derived_recovery

    # The pipeline moved state_init -> derived_recovery (LOC gate, 2026-06-12).
    init_src = inspect.getsource(derived_recovery)
    assert "detect_and_link_live_photo_sidecars" in init_src, (
        "precompute_phashes must tag Live Photo sidecars after hashing — "
        "this is the one path that runs after phashes exist for an "
        "analyze-built library"
    )
    assert "require_phash_match=True" in init_src, (
        "sidecar detection in the phash thread must require a phash match so "
        "a genuinely distinct '_N' photo is never dropped on filename alone"
    )

    worker_src = inspect.getsource(analyze_worker)
    assert "detect_and_link_live_photo_sidecars(" not in worker_src, (
        "analyze worker must NOT call sidecar detection — phash is NULL at "
        "bulk_upsert time, so the phash-confirmation guard would skip every "
        "candidate. Detection belongs in the phash backfill thread."
    )
    # But the worker MUST run the phash pre-scan, which is what actually
    # saves the ~2x scoring cost on a sidecar-heavy library by keeping the
    # confirmed sidecars out of the scoring/face/CLIP passes.
    assert "split_scan_for_confirmed_sidecars" in worker_src, (
        "analyze worker must run the Live Photo phash pre-scan so confirmed "
        "sidecars are skipped from the expensive scoring passes (the perf "
        "complement to the phash-confirmed tagging)"
    )


class TestSplitScanParentAlive:
    """`parent_alive` aligns the pre-scan with detect_and_link's DB-side
    criteria. A parent the DB considers gone (soft-deleted / hidden /
    itself a sidecar) can never anchor a hidden child — the tagger skips
    it — so a candidate confirmed against it would linger as a visible,
    score-less ghost (the DSCF0918 incident: user kept the `_1` copy in
    a duplicate review and trashed the parent; the kept copy froze at
    0% in the lightbox)."""

    def _touch(self, tmp_path, name):
        p = tmp_path / name
        p.write_bytes(b"x")
        return str(p)

    def test_dead_parent_candidate_stays_in_scoring(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "DSCF0918.jpg")
        kept_copy = self._touch(tmp_path, "DSCF0918_1.jpg")
        hashes = {parent: (0xABCD, 0x12), kept_copy: (0xABCD, 0x12)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, kept_copy],
            compute_hashes=lambda p: hashes[p],
            parent_alive=lambda p: p != parent,  # DB says parent is deleted
        )

        assert kept_copy in to_score, (
            "candidate with a dead parent must be scored — the tagger will "
            "never hide it, so skipping scoring strands it at 0%"
        )
        assert records == []

    def test_alive_parent_candidate_still_skipped(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_4214.HEIC")
        sidecar = self._touch(tmp_path, "IMG_4214_1.HEIC")
        hashes = {parent: (0xABCD, 0x12), sidecar: (0xABCD, 0x12)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, sidecar],
            compute_hashes=lambda p: hashes[p],
            parent_alive=lambda p: True,
        )

        assert to_score == [parent]
        assert len(records) == 1 and records[0]["filepath"] == sidecar

    def test_no_parent_alive_callback_preserves_old_behavior(self, tmp_path):
        from bpp.db.live_photo import split_scan_for_confirmed_sidecars

        parent = self._touch(tmp_path, "IMG_4214.HEIC")
        sidecar = self._touch(tmp_path, "IMG_4214_1.HEIC")
        hashes = {parent: (0xABCD, 0x12), sidecar: (0xABCD, 0x12)}

        to_score, records = split_scan_for_confirmed_sidecars(
            [parent, sidecar], compute_hashes=lambda p: hashes[p]
        )

        assert to_score == [parent]
        assert len(records) == 1


class TestSidecarsInvisibleInPhotoGetters:
    """Sidecar rows must NEVER come out of the app-level photo getters.

    Regression (2026-06-12): get_photos_page — which serves
    GET /api/v1/photos, the Library grid — lacked the
    is_live_photo_sidecar filter, so ~3k unscored, dateless duplicate
    placeholder rows flooded the Library view (0% scores, no people,
    no dates). get_all_photos (analysis list + CLI pick) had the same
    gap.
    """

    @pytest.fixture()
    def conn_with_sidecar(self, tmp_path):
        from bpp.db.connection import init_db

        conn = init_db(str(tmp_path / "test.db"))
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, "
            "date, is_live_photo_sidecar) VALUES "
            "('/lib/IMG_1.HEIC', 'IMG_1.HEIC', 1024, 1.0, '2024-06-01T10:00:00', 0), "
            "('/lib/IMG_1_1.HEIC', 'IMG_1_1.HEIC', 1024, 1.0, NULL, 1)"
        )
        conn.commit()
        return conn

    def test_get_all_photos_excludes_sidecars(self, conn_with_sidecar):
        from bpp.db.photos import get_all_photos

        paths = [p["filepath"] for p in get_all_photos(conn_with_sidecar)]
        assert paths == ["/lib/IMG_1.HEIC"], f"sidecar leaked: {paths}"
        # Even the most permissive flags never opt back into sidecars.
        paths = [
            p["filepath"]
            for p in get_all_photos(
                conn_with_sidecar,
                include_missing=True,
                include_deleted=True,
                include_hidden=True,
            )
        ]
        assert paths == ["/lib/IMG_1.HEIC"], f"sidecar leaked via flags: {paths}"

    def test_get_photos_page_excludes_sidecars(self, conn_with_sidecar):
        from bpp.db.photos import get_photos_page

        page = get_photos_page(conn_with_sidecar, limit=100, offset=0, include_deleted=True)
        paths = [p["filepath"] for p in page]
        assert paths == ["/lib/IMG_1.HEIC"], f"sidecar leaked into Library page: {paths}"
