"""Built-in ModelEntry registrations.

Batch 2 of the legal-posture rollout (item 1 — lock SFace as default
face embedder). The two entries registered here are the only face
embedders BPP ships with out-of-the-box metadata; both are
permissively-licensed (Apache 2.0 / Boost SL) so the commercial-use
restriction flag is ``False`` for both. SFace carries
``default_for_kind=True`` so the registry-driven default-selection
layer (Batch 2) lands new installs on SFace with no user action.

The entries record what we know today about each model's upstream
provenance — source URL, terms URL, license summary — without
asserting a legal opinion (item 24 wording). The user-facing labels
come from :mod:`bpp.registry.labels`.

Imported eagerly by :mod:`bpp.registry.__init__` so registration
happens the first time any consumer imports the registry package.
Idempotent: importing twice does not duplicate the entries (the
underlying registry replaces on same id).

Not in scope for this batch:

* Wiring downstream code paths (face_embed.embedding_method,
  worker dispatch) to actually read from the registry — that
  is in Batch 4 (the click-through gate consumes the registry
  via the dialog).
* Registering non-face models (CLIP, YOLO, NudeNet, LaMa, etc.).
  Each kind earns its own registration as the surrounding
  batch lands.

Added post-Batch-10: buffalo_s (item-1 follow-up — first restricted
face embedder registered in the bundled baseline). This validates
the click-through + hard-block + dual-sig + removal-purge surfaces
against a real research-only entry rather than only against the
unit-test scaffolding.
"""

from __future__ import annotations

from bpp.registry.disclaimers import (
    CANONICAL_DISCLAIMER_VERSION,
    PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    canonical_disclaimer_sha256,
    permissive_attribution_disclaimer_sha256,
)
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
    register_entry,
)

# Hash placeholders for the weight files. Real SHA-256 values land
# when Batch 3 (download chokepoint) wires the integrity check against
# the actual downloaded bytes; until then the entries are
# metadata-only and the empty string sentinels signal "no weight-file
# verification configured yet" to consumers.
_NO_WEIGHT_HASH = ""


SFACE_ENTRY = ModelEntry(
    id="sface_yunet",
    display_name="SFace (YuNet + SFace ONNX)",
    kind="face_embedder",
    source_url="https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface",
    terms_url="https://github.com/opencv/opencv_zoo/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/opencv/opencv_zoo/blob/fef72f33ed29bedfaf09ef7d54e4cbbc4d76c7b8/LICENSE"
    ),
    terms_retrieved_at="2026-06-02",
    license_summary=(
        "OpenCV Zoo distribution under Apache 2.0; SFace weights "
        "trained on LFW-derived data. Apache 2.0 permits commercial "
        "use but requires preserving the copyright notice, license "
        "text, and any NOTICE file from the upstream project when "
        "redistributing or shipping a product that includes the model."
    ),
    # Strictest defensible posture (option B from legal-posture
    # discussion): only literal-MIT entries bypass the click-through.
    # Apache 2.0 has NOTICE / attribution duties — surface them once
    # so the user has explicitly seen them.
    requires_explicit_ack=True,
    ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    ack_text_sha256=permissive_attribution_disclaimer_sha256(),
    ack_text_kind="permissive_attribution",
    upstream_claimed_license_class=LicenseClass.APACHE_2_0,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="LFW-derived (OpenCV Zoo distribution)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=True,
    produces_biometric_data=True,
)


DLIB_ENTRY = ModelEntry(
    id="dlib_face_recognition_resnet_v1",
    display_name="dlib face_recognition (ResNet v1)",
    kind="face_embedder",
    source_url="https://github.com/ageitgey/face_recognition_models",
    terms_url="https://github.com/davisking/dlib/blob/master/LICENSE.txt",
    terms_permalink_url=("https://github.com/davisking/dlib/blob/v19.24.2/dlib/LICENSE.txt"),
    terms_retrieved_at="2026-06-02",
    license_summary=(
        "dlib distributed under the Boost Software License; "
        "face_recognition wrapper code is MIT. Pretrained ResNet "
        "weights ship with the face_recognition_models package. "
        "Boost permits commercial use but requires preserving the "
        "license text when redistributing the model or shipping a "
        "product that includes it."
    ),
    # Strictest defensible posture (option B from legal-posture
    # discussion): Boost is functionally MIT but is technically a
    # different license with its own attribution clauses. Surface
    # them once.
    requires_explicit_ack=True,
    ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    ack_text_sha256=permissive_attribution_disclaimer_sha256(),
    ack_text_kind="permissive_attribution",
    upstream_claimed_license_class=LicenseClass.BOOST_SOFTWARE_LICENSE,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="dlib face_recognition private (Davis King)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
    produces_biometric_data=True,
)


#: First restricted-license face embedder in the bundled baseline.
#: Validates every restricted-model surface end-to-end against a real
#: entry: the click-through dialog, the commercial-use hard-block,
#: the dual-signature requirement on relaxation, derived-data purge
#: on removal, and the surface-parity disclaimer wording.
#:
#: Provenance: InsightFace's buffalo_s is part of the model zoo at
#: github.com/deepinsight/insightface. The InsightFace *code* ships
#: under MIT, but the *model weights* are released for non-commercial
#: research purposes only — the README states this directly in the
#: "License" section. That distinction (code permissive, weights
#: restricted) is exactly the case our acceptance-log scaffolding
#: was built for.
#:
#: ``weight_sha256`` is left as the project-wide sentinel until the
#: maintainer downloads the weights and pins the actual hash before
#: a real release (same pattern SFACE_ENTRY and DLIB_ENTRY follow).
#: The integrity check fires only when a non-empty hash is configured,
#: so registering with the sentinel does not weaken the production
#: chain.
BUFFALO_S_ENTRY = ModelEntry(
    id="insightface_buffalo_s",
    display_name="InsightFace buffalo_s (research-only)",
    kind="face_embedder",
    source_url=("https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip"),
    terms_url="https://github.com/deepinsight/insightface/blob/master/README.md",
    terms_permalink_url=(
        # Commit-pinned README so the acceptance log can refer back
        # to the exact License-section wording the user agreed to.
        # Update this permalink whenever ``terms_retrieved_at`` is
        # bumped.
        "https://github.com/deepinsight/insightface/blob/"
        "0b7d8ea7df9dde33c25f7d1f0d8c8e5a3e8e1f3a/README.md"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "InsightFace project code under MIT; the bundled face-analysis "
        "model weights (including buffalo_s) are released for "
        "non-commercial research use only per the project's README "
        "License section. Commercial use requires separately obtained "
        "rights from the upstream maintainer."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data=(
        "MS1MV3 + Glint360K (per InsightFace model-zoo notes); "
        "research-only training-set provenance"
    ),
    # SHA-256 of the upstream buffalo_s.zip release artifact. The
    # loader (bpp.scoring.face_embed_buffalo_s) verifies this hash
    # before extracting w600k_mbf.onnx and verifies the extracted
    # file's own hash before opening the ONNX session. Both layers
    # fail loudly on a mismatch.
    weight_sha256=("d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e"),
    default_for_kind=False,
    ack_text_kind="canonical",
    produces_biometric_data=True,
    # buffalo_s.zip — measured 2026-06-03 against the v0.7 release.
    expected_download_size_bytes=127_607_557,
)


#: ── Face detectors ──
#:
#: SCRFD: InsightFace's small-scale face detector. Code under
#: insightface MIT; the .onnx detector weights ship without a
#: separate license that asserts non-commercial restriction (the
#: research-only clause in the InsightFace README applies to the
#: face-analysis BUNDLES, not to the standalone SCRFD detector).
SCRFD_ENTRY = ModelEntry(
    id="insightface_scrfd_25g",
    display_name="InsightFace SCRFD 2.5g (face detection)",
    kind="face_detector",
    source_url=("https://github.com/deepinsight/insightface/tree/master/detection/scrfd"),
    terms_url="https://github.com/deepinsight/insightface/blob/master/LICENSE",
    terms_permalink_url=(
        "https://github.com/deepinsight/insightface/blob/"
        "0b7d8ea7df9dde33c25f7d1f0d8c8e5a3e8e1f3a/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "InsightFace project distributed under MIT. The SCRFD "
        "detector weights are published under the same MIT license; "
        "the project's non-commercial clause applies to the "
        "face-analysis bundles (buffalo_*), not the standalone "
        "detector."
    ),
    requires_explicit_ack=False,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.MIT,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="WIDER FACE (research dataset)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=True,
    produces_biometric_data=True,
    # 2.5g_bnkps.onnx — measured 2026-06-03 against the HuggingFace mirror.
    expected_download_size_bytes=3_291_737,
)


#: YuNet: OpenCV Zoo face detector. Apache 2.0. Bundled with the
#: install so no separate download UX is needed, but registry-
#: tracked for license-posture parity.
YUNET_ENTRY = ModelEntry(
    id="opencv_yunet",
    display_name="OpenCV YuNet (face detection)",
    kind="face_detector",
    source_url=("https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet"),
    terms_url="https://github.com/opencv/opencv_zoo/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/opencv/opencv_zoo/blob/fef72f33ed29bedfaf09ef7d54e4cbbc4d76c7b8/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "OpenCV Zoo distribution under Apache 2.0. The YuNet "
        "detector weights ship under the same license. Apache 2.0 "
        "permits commercial use but requires preserving the "
        "copyright notice, license text, and any NOTICE file from "
        "the upstream project when redistributing or shipping a "
        "product that includes the model."
    ),
    # Strictest defensible posture (option B): Apache 2.0 attribution
    # duties surfaced once before download.
    requires_explicit_ack=True,
    ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    ack_text_sha256=permissive_attribution_disclaimer_sha256(),
    ack_text_kind="permissive_attribution",
    upstream_claimed_license_class=LicenseClass.APACHE_2_0,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="WIDER FACE (research dataset)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
    produces_biometric_data=True,
    # face_detection_yunet_2023mar.onnx — measured 2026-06-03 against
    # the OpenCV Zoo main branch.
    expected_download_size_bytes=232_589,
)


#: ── Semantic search (CLIP) ──
#:
#: OpenAI CLIP ViT-B/32 ONNX export. Original code + weights under
#: MIT. Sourced from OpenAI's public release.
CLIP_VIT_B32_ENTRY = ModelEntry(
    id="openai_clip_vit_b32_onnx",
    display_name="OpenAI CLIP ViT-B/32 (semantic search)",
    kind="semantic_search",
    source_url="https://github.com/openai/CLIP",
    terms_url="https://github.com/openai/CLIP/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/openai/CLIP/blob/a1d071733d7111c9c014f024669f959182114e33/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "OpenAI CLIP code + weights distributed under MIT. The ONNX "
        "export is a format conversion of the same weights; license "
        "carries over."
    ),
    requires_explicit_ack=False,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.MIT,
    commercial_use_restriction_known=False,
    bppicker_commercial_default_allowed=True,
    commercial_unlock_requires_rights_assertion=False,
    status=ModelStatus.AVAILABLE,
    training_data="OpenAI WebImageText (private, research-sourced)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=True,
    # ViT-B/32 ONNX export — combined visual + text encoder. Measured
    # 2026-06-03 against the deepghs HuggingFace mirror.
    # Visual: 351_650_753 bytes; Text: 253_990_000 bytes.
    expected_download_size_bytes=605_640_753,
)


#: ── Pet detection (YOLOv11n) — RESTRICTED ──
#:
#: Ultralytics YOLOv11 is AGPL-3.0. AGPL attaches to derived works
#: distributed externally and to network-service deployments. A
#: locally-installed user who never redistributes incurs no
#: obligation, but bppicker treats it as restricted because:
#:   * Many users WILL eventually want to share or deploy
#:   * The "I want to use this commercially" gate must fire
#:   * Distributing a Docker image containing the weights triggers
#:     the AGPL source-disclosure obligation
#: Note: ``default_for_kind=False`` despite being the only registered
#: pet detector. The Batch-2 invariant rules out restricted entries
#: as defaults — users must explicitly opt in via the click-through
#: before pet detection runs. Adding a permissive pet detector in
#: the future would make it the default for the kind.
YOLOV11N_PETS_ENTRY = ModelEntry(
    id="ultralytics_yolov11n_pets",
    display_name="Ultralytics YOLOv11n (pet detection, AGPL-3.0)",
    kind="pet_detector",
    source_url="https://github.com/ultralytics/ultralytics",
    terms_url="https://github.com/ultralytics/ultralytics/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/ultralytics/ultralytics/blob/"
        "8b21c14b8c2ce2eb95cc7e2b41ee46e1ef27c97c/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "Ultralytics YOLOv11 (including the YOLOv11n nano variant) "
        "distributed under AGPL-3.0. The AGPL obligations attach to "
        "derived works distributed externally and to "
        "network-service deployments. Personal local use generally "
        "does not trigger source-disclosure; commercial / "
        "distribution scenarios do."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.AGPL_3_0,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data="COCO 2017 (research / annotated)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
    ack_text_kind="canonical",
    # yolo11n.onnx — measured 2026-06-03 against the Ultralytics
    # GitHub release artifact.
    expected_download_size_bytes=10_930_182,
)


#: ── Nudity classifier (NudeNet) — RESTRICTED ──
#:
#: NudeNet 320n. The package is published as GPL-3.0; the model
#: weights are distributed as part of the same release. Same
#: restricted-default treatment as YOLOv11n.
NUDENET_320N_ENTRY = ModelEntry(
    id="nudenet_320n",
    display_name="NudeNet 320n (nudity classifier, GPL-3.0)",
    kind="nudity_classifier",
    source_url="https://github.com/notAI-tech/NudeNet",
    terms_url="https://github.com/notAI-tech/NudeNet/blob/v3/LICENSE",
    terms_permalink_url=(
        # NudeNet v3 LICENSE permalink; verified GPL-3.0.
        "https://github.com/notAI-tech/NudeNet/blob/"
        "ddc4810ff6a99d1b7baf2027cc15a6e0e69c5b9b/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "NudeNet code + bundled model weights distributed under "
        "GPL-3.0. The strong copyleft attaches to derived works "
        "distributed externally; personal local use does not "
        "trigger source-disclosure but commercial / distribution "
        "scenarios do."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.GPL_3_0,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data="NudeNet private research dataset",
    # SHA-256 of 320n.onnx fetched from the commit-pinned URL in
    # ``bpp.scoring.nudity.NUDENET_MODEL_URL``. ``download_file``
    # verifies this before the bytes are written to disk.
    weight_sha256=("c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f"),
    default_for_kind=False,
    ack_text_kind="canonical",
    produces_biometric_data=False,
    # 320n.onnx — measured 2026-06-04 against the upstream raw
    # githubusercontent.com mirror.
    expected_download_size_bytes=12_150_158,
)


#: ── Inpainting (LaMa) — RESTRICTED ──
#:
#: simple-lama-inpainting wrapper code is Apache 2.0; the LaMa
#: model weights come from advimman/lama which is "research only,
#: non-commercial". Same restricted-default treatment as buffalo_s.
LAMA_INPAINT_ENTRY = ModelEntry(
    id="lama_inpaint_research",
    display_name="LaMa inpainting (research weights, non-commercial)",
    kind="inpainter",
    source_url="https://github.com/advimman/lama",
    terms_url="https://github.com/advimman/lama/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/advimman/lama/blob/7dee0e4a3cf5f73f86a820674bf471454f52b74f/LICENSE"
    ),
    terms_retrieved_at="2026-06-03",
    license_summary=(
        "LaMa research code distributed under Apache 2.0; the "
        "pretrained model weights are released for non-commercial "
        "research use only per the advimman/lama project's README "
        "and CC-BY-NC license header. The wrapper package "
        "simple-lama-inpainting is Apache 2.0 in its own right."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data="Places2 (research)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
    ack_text_kind="canonical",
    # big-lama.pt — measured 2026-06-03 against the simple-lama-
    # inpainting v0.1.0 release artifact.
    expected_download_size_bytes=205_803_670,
)


def register_builtins() -> None:
    """Register the built-in face-embedder entries.

    Called at import time by :mod:`bpp.registry.__init__`. Idempotent
    in practice — the underlying registry replaces on same id, so
    re-importing the package does not produce duplicate entries.
    """
    register_entry(SFACE_ENTRY)
    register_entry(DLIB_ENTRY)
    register_entry(BUFFALO_S_ENTRY)
    # Face detectors.
    register_entry(SCRFD_ENTRY)
    register_entry(YUNET_ENTRY)
    # Semantic search.
    register_entry(CLIP_VIT_B32_ENTRY)
    # Restricted models (pet detection, nudity classifier, inpainting).
    register_entry(YOLOV11N_PETS_ENTRY)
    register_entry(NUDENET_320N_ENTRY)
    register_entry(LAMA_INPAINT_ENTRY)
