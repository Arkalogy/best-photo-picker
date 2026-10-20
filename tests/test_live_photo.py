"""Tests for Live Photo sidecar detection and filtering — part 1 of 2."""

from __future__ import annotations


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
        don't waste a decode on the candidate."""
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
        scoring. Strict both-hashes guard."""
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
        whole library for no payoff. Worst-case decode work is bounded
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
        """LOAD-BEARING INVARIANT: every stored sidecar row must be
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
