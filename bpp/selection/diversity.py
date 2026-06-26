"""Diversity constraints for photo selection."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from bpp.dedupe.phash import dual_hash_distance, hamming_distance


class DiversityTracker:
    """Track diversity constraints during greedy selection.

    Enforces three kinds of diversity:
      1. Temporal: max photos per day / per month
      2. Visual (CLIP): reject if cosine similarity >= threshold to any accepted photo
      3. Visual (hash fallback): reject if hash distance <= threshold to any accepted photo
    """

    # Hash distance used when CLIP embeddings are unavailable.
    # Looser than dedup (10/8) — catches "same scene, different take".
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
        # Accepted items for similarity comparison
        self._accepted_clip: list[np.ndarray] = []
        self._accepted_hashes: list[tuple[int | None, int | None]] = []

    def set_available_months(self, months: set[str]) -> None:
        self.all_months = months

    def is_too_similar(self, item: dict[str, Any]) -> bool:
        """Check if item is visually too similar to any already-accepted photo.

        Uses both CLIP (semantic) and hash (perceptual) checks when available.
        A photo is rejected if *either* method flags it as too similar.
        """
        if self.similarity_threshold <= 0:
            return False

        # CLIP check (semantic similarity)
        photo_id = item.get("id")
        emb = self.clip_embeddings.get(photo_id) if photo_id is not None else None
        if emb is not None and self._accepted_clip:
            for accepted_emb in self._accepted_clip:
                sim = float(np.dot(emb, accepted_emb))
                if sim >= self.similarity_threshold:
                    return True

        # Hash check (perceptual similarity) — catches near-identical framing
        # that CLIP may consider semantically distinct
        phash = item.get("phash")
        if phash is not None and self._accepted_hashes:
            ahash = item.get("ahash")
            for acc_ph, acc_ah in self._accepted_hashes:
                if acc_ph is None:
                    continue
                if acc_ah is not None and ahash is not None:
                    dist = dual_hash_distance(phash, ahash, acc_ph, acc_ah)
                else:
                    dist = hamming_distance(phash, acc_ph)
                if dist <= self.HASH_FALLBACK_DISTANCE:
                    return True

        return False

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
        if emb is not None:
            self._accepted_clip.append(emb)
        self._accepted_hashes.append((item.get("phash"), item.get("ahash")))

        reasons = []
        if self.month_counts[month] == 1:
            reasons.append(f"first photo for {month}")

        reasons.append(f"score={item.get('aggregate_score', 0):.3f}")
        reasons.append(f"day {day} ({self.day_counts[day]}/{self.max_per_day})")

        return "; ".join(reasons)

    def uncovered_months(self) -> set[str]:
        """Return months with no selected photos yet."""
        return self.all_months - set(self.month_counts.keys())
