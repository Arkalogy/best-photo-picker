"""Built-in ModelEntry registrations.

Covers every model BPP knows about out of the box: face embedders
(SFace, dlib, buffalo_s), face detectors (SCRFD, YuNet), the CLIP
semantic-search encoder, the pet detector, the nudity classifier and
the LaMa inpainter, plus the supporting models in
:mod:`bpp.registry.builtins_bundled`. This registry is the only place
a model's license is written; scoring code and UI labels link to an
entry by id. SFace (Apache 2.0) carries ``default_for_kind=True`` so
new installs land on SFace as the face embedder; dlib is restricted
for commercial use because of its landmark model's training data.

The entries record what we know today about each model's upstream
provenance — source URL, terms URL, license summary — without
asserting a legal opinion. The user-facing labels come from
:mod:`bpp.registry.labels`.

Imported eagerly by :mod:`bpp.registry.__init__` so registration
happens the first time any consumer imports the registry package.
Idempotent: importing twice does not duplicate the entries (the
underlying registry replaces on same id).

buffalo_s is the restricted (research-only) face embedder in the
bundled baseline. It exercises the click-through + hard-block +
signed-overlay quorum + removal-purge surfaces against a real restricted entry
rather than only against unit-test scaffolding.
"""

from __future__ import annotations

from bpp.registry.builtins_bundled import BUNDLED_ENTRIES
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

# Hash placeholder for weight files the registry does not pin. Entries
# using it are metadata-only from the registry's point of view; the
# empty-string sentinel signals "no registry weight-file verification
# configured" to consumers.
_NO_WEIGHT_HASH = ""


SFACE_ENTRY = ModelEntry(
    id="sface_yunet",
    display_name="SFace (YuNet + SFace ONNX)",
    kind="face_embedder",
    source_url="https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface",
    terms_url="https://github.com/opencv/opencv_zoo/blob/main/LICENSE",
    terms_permalink_url=(
        "https://github.com/opencv/opencv_zoo/blob/af1afb38ffa9f7b054a746d73ebf2d6e1ae2eb24/LICENSE"
    ),
    terms_retrieved_at="2026-10-13",
    license_summary=(
        "OpenCV Zoo distribution under Apache 2.0; SFace weights "
        "trained on LFW-derived data. Apache 2.0 permits commercial "
        "use but requires preserving the copyright notice, license "
        "text, and any NOTICE file from the upstream project when "
        "redistributing or shipping a product that includes the model."
    ),
    # Strictest defensible posture: only literal-MIT entries bypass the click-through.
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
    terms_retrieved_at="2026-10-13",
    license_summary=(
        "dlib is Boost-licensed and the face_recognition wrapper is MIT, "
        "but the 68-point landmark model in face_recognition_models was "
        "trained on the iBUG 300-W dataset, whose creators ask that it "
        "not be used in commercial products. BPP treats these weights "
        "as restricted for commercial use and does not bundle them in "
        "the desktop app. This entry also covers dlib's HOG face "
        "detector, which BPP runs through the same face_recognition "
        "package (importing it loads the landmark model)."
    ),
    # Restricted: the 300-W non-commercial request on the landmark
    # model. Same treatment as the other research-trained weights —
    # click-through, and blocked in a "Commercial" use context unless
    # the user states they hold separate rights.
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    ack_text_kind="canonical",
    # Code is Boost and the package says CC0, but the landmark model's
    # training-data terms exclude commercial use — classified by that.
    upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data="dlib face_recognition private (Davis King)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
    produces_biometric_data=True,
)


#: First restricted-license face embedder in the bundled baseline.
#: Validates every restricted-model surface end-to-end against a real
#: entry: the click-through dialog, the commercial-use hard-block,
#: the signed-overlay quorum on relaxation, derived-data purge
#: on removal, and the surface-parity disclaimer wording.
#:
#: Provenance: InsightFace's buffalo_s is part of the model zoo at
#: github.com/deepinsight/insightface. The InsightFace *code* ships
#: under MIT, but the *model weights* are released for non-commercial
#: research purposes only — the README states this directly in the
#: "License" section. That distinction (code permissive, weights
#: restricted) is exactly the case our acceptance-log scaffolding
#: was built for.
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
        "7aaa8e52fa787eb27b87d654a268483a53505c59/README.md"
    ),
    terms_retrieved_at="2026-10-13",
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
#: SCRFD: InsightFace's small-scale face detector — RESTRICTED.
#: InsightFace's code is MIT, but its README states that models
#: trained on its data, whether downloaded by hand from the GitHub
#: repo or through the python library, are for non-commercial
#: research purposes only. That covers these detector weights, so
#: SCRFD gets the same restricted treatment as buffalo_s: explicit
#: acceptance before download or use, blocked in commercial mode,
#: and not the default detector.
SCRFD_ENTRY = ModelEntry(
    id="insightface_scrfd_25g",
    display_name="InsightFace SCRFD 2.5g (face detection)",
    kind="face_detector",
    source_url=(
        "https://huggingface.co/RuteNL/SCRFD-face-detection-ONNX/resolve/main/2.5g_bnkps.onnx"
    ),
    terms_url="https://github.com/deepinsight/insightface/blob/master/README.md#license",
    terms_permalink_url=(
        "https://github.com/deepinsight/insightface/blob/"
        "7aaa8e52fa787eb27b87d654a268483a53505c59/README.md"
    ),
    terms_retrieved_at="2026-10-13",
    license_summary=(
        "InsightFace code is MIT, but its trained models are for "
        "non-commercial research use only per the project's README. "
        "Downloaded from a third-party Hugging Face re-host of the "
        "InsightFace SCRFD 2.5g weights."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
    commercial_use_restriction_known=True,
    bppicker_commercial_default_allowed=False,
    commercial_unlock_requires_rights_assertion=True,
    status=ModelStatus.AVAILABLE,
    training_data="WIDER FACE (research dataset)",
    weight_sha256=_NO_WEIGHT_HASH,
    default_for_kind=False,
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
        "https://github.com/opencv/opencv_zoo/blob/af1afb38ffa9f7b054a746d73ebf2d6e1ae2eb24/LICENSE"
    ),
    terms_retrieved_at="2026-10-13",
    license_summary=(
        "OpenCV Zoo distribution under Apache 2.0. The YuNet "
        "detector weights ship under the same license. Apache 2.0 "
        "permits commercial use but requires preserving the "
        "copyright notice, license text, and any NOTICE file from "
        "the upstream project when redistributing or shipping a "
        "product that includes the model."
    ),
    # Strictest defensible posture: Apache 2.0 attribution
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
    default_for_kind=True,
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
#: pet detector. The registry invariant rules out restricted entries
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
        "2c6fc0a4443b9cf805ef17b1cfdd71a98693b4d4/LICENSE"
    ),
    terms_retrieved_at="2026-10-13",
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
#: NudeNet 320n. The repository LICENSE is AGPL-3.0 (its PyPI
#: metadata says MIT; BPP follows the LICENSE file); the model
#: weights are distributed as part of the same release. Same
#: restricted-default treatment as YOLOv11n.
NUDENET_320N_ENTRY = ModelEntry(
    id="nudenet_320n",
    display_name="NudeNet 320n (nudity classifier, AGPL-3.0)",
    kind="nudity_classifier",
    source_url="https://github.com/notAI-tech/NudeNet",
    terms_url="https://github.com/notAI-tech/NudeNet/blob/v3/LICENSE",
    terms_permalink_url=(
        # NudeNet v3 LICENSE permalink; verified AGPL-3.0.
        "https://github.com/notAI-tech/NudeNet/blob/"
        "bac927ae7ef3ea6b57c175e3fb36686a1115db16/LICENSE"
    ),
    terms_retrieved_at="2026-10-13",
    license_summary=(
        "NudeNet code + bundled model weights distributed under "
        "AGPL-3.0. The strong copyleft attaches to derived works "
        "distributed externally; personal local use does not "
        "trigger source-disclosure but commercial / distribution "
        "scenarios do."
    ),
    requires_explicit_ack=True,
    ack_text_version=CANONICAL_DISCLAIMER_VERSION,
    ack_text_sha256=canonical_disclaimer_sha256(),
    upstream_claimed_license_class=LicenseClass.AGPL_3_0,
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
    # Supporting models every analysis runs (MediaPipe, Haar cascade).
    for entry in BUNDLED_ENTRIES:
        register_entry(entry)
