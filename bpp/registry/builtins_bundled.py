"""Registry entries for the supporting models every analysis runs.

These are the MediaPipe face / hand / pose / segmentation models and
OpenCV's Haar face cascade. They are not user-pickable alternatives —
they run alongside the default detector and embedder — but they are
third-party weights with their own license terms, so each one has a
registry entry like every other model BPP runs. The entry is the only
place the license is written; scoring code links to it by id.

All of them are Apache 2.0 (MediaPipe) or the Intel open-source
license (a BSD-3-Clause-style license, for the Haar cascade). Both
carry attribution duties, so each uses the permissive-attribution
acknowledgment that SFace and YuNet use. New users accept all of them
together in one step before their first analysis
(:mod:`bpp.registry.attribution_bundle`).
"""

from __future__ import annotations

from bpp.registry.disclaimers import (
    PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    permissive_attribution_disclaimer_sha256,
)
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
)

# MediaPipe repository LICENSE pinned to a commit, so an acceptance row
# can point at the exact wording years later. Each model's own card
# (the ``terms_url``) states "Licensed under Apache License, Version 2.0".
_MEDIAPIPE_LICENSE_PERMALINK = (
    "https://github.com/google-ai-edge/mediapipe/blob/"
    "b22a2777fc44a4cce567154469799025c69512e8/LICENSE"
)
_MODEL_CARDS = "https://storage.googleapis.com/mediapipe-assets/"
_RETRIEVED = "2026-10-14"


def _mediapipe_entry(
    *,
    id: str,
    display_name: str,
    kind: str,
    source_url: str,
    model_card: str,
    training_data: str,
    default_for_kind: bool,
    runs_alongside_default: bool,
    produces_biometric_data: bool,
) -> ModelEntry:
    return ModelEntry(
        id=id,
        display_name=display_name,
        kind=kind,
        source_url=source_url,
        terms_url=_MODEL_CARDS + model_card,
        terms_permalink_url=_MEDIAPIPE_LICENSE_PERMALINK,
        terms_retrieved_at=_RETRIEVED,
        license_summary=(
            "Google MediaPipe model released under Apache 2.0 (per its "
            "model card). Apache 2.0 permits commercial use but requires "
            "preserving the copyright notice, license text, and any "
            "NOTICE file when redistributing the model."
        ),
        requires_explicit_ack=True,
        ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
        ack_text_sha256=permissive_attribution_disclaimer_sha256(),
        ack_text_kind="permissive_attribution",
        upstream_claimed_license_class=LicenseClass.APACHE_2_0,
        commercial_use_restriction_known=False,
        bppicker_commercial_default_allowed=True,
        commercial_unlock_requires_rights_assertion=False,
        status=ModelStatus.AVAILABLE,
        training_data=training_data,
        # The scoring modules pin and verify the SHA-256 of each file.
        weight_sha256="",
        default_for_kind=default_for_kind,
        runs_alongside_default=runs_alongside_default,
        produces_biometric_data=produces_biometric_data,
    )


BLAZEFACE_SHORT_RANGE_ENTRY = _mediapipe_entry(
    id="mediapipe_blazeface_short_range",
    display_name="MediaPipe BlazeFace short-range (face detection)",
    kind="face_detector",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector",
    model_card="MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf",
    training_data="Google-collected face images (per the model card)",
    default_for_kind=False,
    runs_alongside_default=True,
    produces_biometric_data=True,
)

BLAZEFACE_FULL_RANGE_ENTRY = _mediapipe_entry(
    id="mediapipe_blazeface_full_range",
    display_name="MediaPipe BlazeFace full-range (face detection)",
    kind="face_detector",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector",
    model_card="MediaPipe%20BlazeFace%20Model%20Card%20(Full%20Range).pdf",
    training_data="Google-collected face images (per the model card)",
    default_for_kind=False,
    runs_alongside_default=True,
    produces_biometric_data=True,
)

FACE_LANDMARKER_ENTRY = _mediapipe_entry(
    id="mediapipe_face_landmarker",
    display_name="MediaPipe Face Landmarker (expressions)",
    kind="face_landmarker",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker",
    model_card="Model%20Card%20Blendshape%20V2.pdf",
    training_data="Google-collected face images (per the model card)",
    default_for_kind=True,
    runs_alongside_default=False,
    produces_biometric_data=True,
)

HAND_LANDMARKER_ENTRY = _mediapipe_entry(
    id="mediapipe_hand_landmarker",
    display_name="MediaPipe Hand Landmarker (hand filter)",
    kind="hand_landmarker",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker",
    model_card="Model%20Card%20Hand%20Tracking%20(Lite_Full)%20with%20Fairness%20Oct%202021.pdf",
    training_data="Google-collected hand images (per the model card)",
    default_for_kind=True,
    runs_alongside_default=False,
    # Hand geometry is a biometric identifier in some jurisdictions.
    produces_biometric_data=True,
)

POSE_LANDMARKER_ENTRY = _mediapipe_entry(
    id="mediapipe_pose_landmarker_lite",
    display_name="MediaPipe Pose Landmarker lite (pose)",
    kind="pose_estimator",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker",
    model_card="Model%20Card%20BlazePose%20GHUM%203D.pdf",
    training_data="Google-collected body images (per the model card)",
    default_for_kind=True,
    runs_alongside_default=False,
    produces_biometric_data=False,
)

SELFIE_SEGMENTER_ENTRY = _mediapipe_entry(
    id="mediapipe_selfie_segmenter",
    display_name="MediaPipe Selfie Segmenter (subject segmentation)",
    kind="segmenter",
    source_url="https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter",
    model_card="Model%20Card%20MediaPipe%20Selfie%20Segmentation.pdf",
    training_data="Google-collected images of people (per the model card)",
    default_for_kind=True,
    runs_alongside_default=False,
    produces_biometric_data=False,
)

#: OpenCV's frontal-face Haar cascade, shipped inside opencv-python.
#: The XML file's own header carries the "Intel License Agreement For
#: Open Source Computer Vision Library" — three BSD-style conditions
#: (keep the notice, reproduce it in binary distributions, no Intel
#: endorsement), so it is classed as BSD 3-Clause.
HAAR_FRONTALFACE_ENTRY = ModelEntry(
    id="opencv_haar_frontalface",
    display_name="OpenCV Haar cascade (face detection)",
    kind="face_detector",
    source_url=(
        "https://github.com/opencv/opencv/blob/4.x/data/haarcascades/"
        "haarcascade_frontalface_default.xml"
    ),
    terms_url=(
        "https://github.com/opencv/opencv/blob/4.x/data/haarcascades/"
        "haarcascade_frontalface_default.xml"
    ),
    terms_permalink_url=(
        "https://github.com/opencv/opencv/blob/"
        "0332904392156fad0ba3248c8be25b9853a73216/data/haarcascades/"
        "haarcascade_frontalface_default.xml"
    ),
    terms_retrieved_at=_RETRIEVED,
    license_summary=(
        "Intel License Agreement for the Open Source Computer Vision "
        "Library (a BSD-3-Clause-style license), stated in the cascade "
        "file's header. Permits commercial use; redistribution must "
        "keep the copyright notice and conditions, and Intel's name "
        "may not be used to endorse derived products."
    ),
    requires_explicit_ack=True,
    ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    ack_text_sha256=permissive_attribution_disclaimer_sha256(),
    ack_text_kind="permissive_attribution",
    upstream_claimed_license_class=LicenseClass.BSD_3_CLAUSE,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="Frontal face images (Rainer Lienhart, 2000s)",
    weight_sha256="",
    default_for_kind=False,
    # A last-resort fallback: runs only when the other detectors can't.
    runs_alongside_default=False,
    ships_with_runtime=True,
    produces_biometric_data=True,
)


BUNDLED_ENTRIES: tuple[ModelEntry, ...] = (
    BLAZEFACE_SHORT_RANGE_ENTRY,
    BLAZEFACE_FULL_RANGE_ENTRY,
    FACE_LANDMARKER_ENTRY,
    HAND_LANDMARKER_ENTRY,
    POSE_LANDMARKER_ENTRY,
    SELFIE_SEGMENTER_ENTRY,
    HAAR_FRONTALFACE_ENTRY,
)
