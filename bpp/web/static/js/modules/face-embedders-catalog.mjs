// @ts-check
/**
 * Static tables for the Settings → Models picker: which legacy
 * install-state row each registry entry joins to, the per-kind group
 * titles, and the order kinds are shown in. Split out of
 * modals-face-embedders.mjs for the 500-LOC cap.
 */

/**
 * Join table: registry id → legacy feature label and per-feature
 * toggle key (when applicable). Used to overlay install state on the
 * registry picker. Transitional — when each model's file-install
 * logic lands in the registry layer this mapping goes away.
 *
 * Keys NOT in this map render without install controls (registry-
 * catalog-only entries). Values without a ``toggle_key`` render
 * without an enable toggle.
 */
export const LEGACY_FEATURE_MAP = {
  // Face embedders
  sface_yunet: { legacy_label: "Face recognition", toggle_key: null },
  dlib_face_recognition_resnet_v1: {
    legacy_label: "Face recognition",
    toggle_key: null,
  },
  insightface_buffalo_s: { legacy_label: null, toggle_key: null },
  // Face detectors
  insightface_scrfd_25g: {
    legacy_label: "SCRFD face detection",
    toggle_key: "model_scrfd",
  },
  opencv_yunet: {
    legacy_label: "Face detection",
    toggle_key: null,
  },
  mediapipe_blazeface_short_range: {
    legacy_label: "Face detection",
    toggle_key: null,
  },
  mediapipe_blazeface_full_range: {
    legacy_label: "BlazeFace full-range",
    toggle_key: "model_blazeface_fr",
  },
  // Supporting models (expressions, hand filter, pose, segmentation)
  mediapipe_face_landmarker: {
    legacy_label: "Expression scoring",
    toggle_key: "model_face_landmarker",
  },
  mediapipe_hand_landmarker: {
    legacy_label: "Hand suppression",
    toggle_key: "model_hand_landmarker",
  },
  mediapipe_pose_landmarker_lite: {
    legacy_label: "Pose estimation",
    toggle_key: "model_pose",
  },
  mediapipe_selfie_segmenter: {
    legacy_label: "Subject segmentation",
    toggle_key: "model_segmentation",
  },
  // Semantic search
  openai_clip_vit_b32_onnx: {
    legacy_label: "Smart search & dedup",
    toggle_key: null,
  },
  // Pet detection
  ultralytics_yolov11n_pets: {
    legacy_label: "Pet detection",
    toggle_key: null,
  },
  // Nudity classifier
  nudenet_320n: { legacy_label: "Content filter", toggle_key: null },
  // Inpainting
  lama_inpaint_research: {
    legacy_label: "AI object removal",
    toggle_key: null,
  },
};

export const KIND_LABELS = {
  face_embedder: "Face embedders",
  face_detector: "Face detectors",
  face_landmarker: "Expression scoring",
  hand_landmarker: "Hand filter",
  pose_estimator: "Pose estimation",
  segmenter: "Subject segmentation",
  semantic_search: "Semantic search",
  pet_detector: "Pet detection",
  nudity_classifier: "Content filter",
  inpainter: "AI object removal",
};

/** Stable kind ordering for the UI. */
export const KIND_ORDER = [
  "face_embedder",
  "face_detector",
  "face_landmarker",
  "hand_landmarker",
  "pose_estimator",
  "segmenter",
  "semantic_search",
  "pet_detector",
  "nudity_classifier",
  "inpainter",
];
