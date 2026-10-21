"""Diversity constraints for photo selection."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

#: Popcount of each byte value, for a vectorized Hamming distance.
_POPCOUNT8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
_NO_HASH = np.uint64(0)


def _hamming_many(value: int, others: np.ndarray) -> np.ndarray:
    """Hamming distance from one 64-bit hash to an array of them."""
    x = np.bitwise_xor(others, np.uint64(value & 0xFFFFFFFFFFFFFFFF))
    return _POPCOUNT8[x.view(np.uint8)].reshape(len(others), 8).sum(axis=1)


class DiversityTracker:
    """Track diversity constraints during greedy selection.

    Enforces three kinds of diversity:
      1. Temporal: max photos per day / per month
      2. Visual (CLIP): reject if cosine similarity >= threshold to any accepted photo
      3. Visual (hash fallback): reject if hash distance <= threshold to any accepted photo
    """

    # Hash distance used when CLIP embeddings are unavailable — catches
    # "same scene, different take" among the picks (dedup's own thresholds
    # are the `hash_distance_threshold` / `global_hash_distance_threshold`
    # config keys).
    HASH_FALLBACK_DISTANCE = 16

    def __init__(
        self,
        max_per_day: int = 3,
        min_per_month: int = 1,
        max_per_month: int = 0,
        similarity_threshold: float = 0.0,
        clip_embeddings: dict[int, np.ndarray] | None = None,
    ) -> None:
        self.max_per_day = max_per_day
        self.min_per_month = min_per_month
        self.max_per_month = max_per_month  # 0 = unlimited
        self.similarity_threshold = similarity_threshold
        self.clip_embeddings = clip_embeddings or {}
        self.day_counts: Counter[str] = Counter()
        self.month_counts: Counter[str] = Counter()
        self.all_months: set[str] = set()
        # Accepted items for similarity comparison: one matrix for the
        # CLIP vectors (grown in steps; one matrix-vector product per
        # candidate) and uint64 arrays for the hashes (a vectorized
        # Hamming distance). A pure-Python loop over every accepted
        # photo per candidate cost minutes per recompute at k=10k.
        self._clip_rows: np.ndarray | None = None
        self._n_clip = 0
        self._phash = np.empty(0, dtype=np.uint64)
        self._phash_valid = np.empty(0, dtype=bool)
        self._ahash = np.empty(0, dtype=np.uint64)
        self._ahash_valid = np.empty(0, dtype=bool)
        self._n_hash = 0

    def set_available_months(self, months: set[str]) -> None:
        self.all_months = months

    def is_too_similar(self, item: dict[str, Any]) -> bool:
        """Check if item is visually too similar to any already-accepted photo.

        Uses both CLIP (semantic) and hash (perceptual) checks when available.
        A photo is rejected if *either* method flags it as too similar.
        """
        if self.similarity_threshold <= 0:
            return False

        # CLIP check (semantic similarity): one product against every
        # accepted vector.
        photo_id = item.get("id")
        emb = self.clip_embeddings.get(photo_id) if photo_id is not None else None
        if emb is not None and self._n_clip and self._clip_rows is not None:
            sims = self._clip_rows[: self._n_clip] @ np.asarray(emb, dtype=np.float32)
            if float(sims.max()) >= self.similarity_threshold:
                return True

        # Hash check (perceptual similarity) — catches near-identical framing
        # that CLIP may consider semantically distinct. dual distance (the
        # smaller of the two) when both sides have both hashes, else phash.
        phash = item.get("phash")
        if phash is not None and self._n_hash:
            n = self._n_hash
            valid = self._phash_valid[:n]
            if valid.any():
                acc_ph = self._phash[:n][valid]
                dist = _hamming_many(phash, acc_ph)
                ahash = item.get("ahash")
                if ahash is not None:
                    both = self._ahash_valid[:n][valid]
                    if both.any():
                        d_a = _hamming_many(ahash, self._ahash[:n][valid][both])
                        dist = dist.astype(np.int64)
                        dist[both] = np.minimum(dist[both], d_a)
                if int(dist.min()) <= self.HASH_FALLBACK_DISTANCE:
                    return True

        return False

    def _grow(self, emb_dim: int | None) -> None:
        """Make room for one more accepted photo in every array."""
        if self._n_hash >= len(self._phash):
            new = max(256, len(self._phash) * 2)
            for name in ("_phash", "_ahash"):
                arr = np.empty(new, dtype=np.uint64)
                arr[: self._n_hash] = getattr(self, name)[: self._n_hash]
                setattr(self, name, arr)
            for name in ("_phash_valid", "_ahash_valid"):
                arr = np.zeros(new, dtype=bool)
                arr[: self._n_hash] = getattr(self, name)[: self._n_hash]
                setattr(self, name, arr)
        if emb_dim is not None and (
            self._clip_rows is None or self._n_clip >= len(self._clip_rows)
        ):
            new = max(256, (0 if self._clip_rows is None else len(self._clip_rows)) * 2)
            rows = np.empty((new, emb_dim), dtype=np.float32)
            if self._clip_rows is not None:
                rows[: self._n_clip] = self._clip_rows[: self._n_clip]
            self._clip_rows = rows

    def can_accept(self, item: dict[str, Any]) -> tuple[bool, str]:
        """Check if item can be accepted under diversity constraints.

        Returns (accepted, reason).
        """
        day = item.get("date_day", "unknown")
        month = item.get("date_month", "unknown")

        if self.day_counts[day] >= self.max_per_day:
            return False, f"day {day} already has {self.max_per_day} photos"

        if self.max_per_month > 0 and self.month_counts[month] >= self.max_per_month:
            return False, f"month {month} already has {self.max_per_month} photos"

        if self.is_too_similar(item):
            return False, "too similar to already-selected photo"

        return True, ""

    def accept(self, item: dict[str, Any]) -> str:
        """Record acceptance and return selection reason."""
        day = item.get("date_day", "unknown")
        month = item.get("date_month", "unknown")

        self.day_counts[day] += 1
        self.month_counts[month] += 1

        # Track for similarity checks
        photo_id = item.get("id")
        emb = self.clip_embeddings.get(photo_id) if photo_id is not None else None
        self._grow(None if emb is None else int(np.asarray(emb).shape[0]))
        if emb is not None and self._clip_rows is not None:
            self._clip_rows[self._n_clip] = np.asarray(emb, dtype=np.float32)
            self._n_clip += 1
        i = self._n_hash
        phash, ahash = item.get("phash"), item.get("ahash")
        self._phash[i] = np.uint64(phash & 0xFFFFFFFFFFFFFFFF) if phash is not None else _NO_HASH
        self._phash_valid[i] = phash is not None
        self._ahash[i] = np.uint64(ahash & 0xFFFFFFFFFFFFFFFF) if ahash is not None else _NO_HASH
        self._ahash_valid[i] = ahash is not None
        self._n_hash += 1

        reasons = []
        if self.month_counts[month] == 1:
            reasons.append(f"first photo for {month}")

        reasons.append(f"score={item.get('aggregate_score', 0):.3f}")
        reasons.append(f"day {day} ({self.day_counts[day]}/{self.max_per_day})")

        return "; ".join(reasons)

    def uncovered_months(self) -> set[str]:
        """Return months with no selected photos yet."""
        return self.all_months - set(self.month_counts.keys())
