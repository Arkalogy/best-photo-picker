"""When SFace finds no face, the photo has no face: no dlib second try.
dlib's vectors are on another scale than the library's SFace groups, and
in an install without dlib (the Mac app) every faceless photo became a
failed photo."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np


def test_no_face_from_sface_is_no_face_without_asking_dlib():
    from bpp.scoring import face_embed, face_embed_sface

    img = np.zeros((32, 32, 3), dtype=np.uint8)
    with (
        patch.object(face_embed_sface, "_get_sface_recognizer", return_value=object()),
        patch("bpp.scoring.face_embed_detect.detect_face_rows", return_value=[]),
        patch.object(face_embed, "_extract_dlib") as dlib,
    ):
        assert face_embed.extract_face_embeddings(img, method="sface") == []
    dlib.assert_not_called()


def test_a_missing_dlib_install_does_not_fail_a_faceless_photo():
    from bpp.scoring import face_embed, face_embed_sface

    img = np.zeros((32, 32, 3), dtype=np.uint8)
    with (
        patch.object(face_embed_sface, "_get_sface_recognizer", return_value=object()),
        patch("bpp.scoring.face_embed_detect.detect_face_rows", return_value=[]),
        patch.object(face_embed, "_extract_dlib", side_effect=ImportError("not installed")),
    ):
        assert face_embed.extract_face_embeddings(img, method="sface") == []
