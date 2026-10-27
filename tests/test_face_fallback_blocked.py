"""SFace found no face and the dlib second try is blocked by its license:
the photo has no face, it is not a failed photo."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from bpp.registry.policy import ModelLoadBlockedError


def test_a_blocked_dlib_second_try_means_no_face_not_a_failure():
    from bpp.scoring import face_embed

    def blocked(*_a, **_k):
        raise ModelLoadBlockedError.__new__(ModelLoadBlockedError)

    img = np.zeros((32, 32, 3), dtype=np.uint8)
    with (
        patch.object(face_embed, "_extract_sface", return_value=None),
        patch.object(face_embed, "_extract_dlib", side_effect=blocked),
        patch.object(face_embed, "_get_sface_recognizer", return_value=object()),
    ):
        assert face_embed.extract_face_embeddings(img, method="sface") == []


def test_with_sface_itself_missing_a_blocked_dlib_is_still_a_failure():
    import pytest

    from bpp.scoring import face_embed

    def blocked(*_a, **_k):
        raise ModelLoadBlockedError.__new__(ModelLoadBlockedError)

    img = np.zeros((32, 32, 3), dtype=np.uint8)
    with (
        patch.object(face_embed, "_extract_sface", return_value=None),
        patch.object(face_embed, "_extract_dlib", side_effect=blocked),
        patch.object(face_embed, "_get_sface_recognizer", return_value=None),
        pytest.raises(ModelLoadBlockedError),
    ):
        face_embed.extract_face_embeddings(img, method="sface")
