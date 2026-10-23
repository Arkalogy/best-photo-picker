// @ts-check
/**
 * Static tables for the Settings → Models picker: which legacy
 * install-state row each registry entry joins to, the per-kind group
 * titles, and the order kinds are shown in. Split out of
 * modals-face-embedders.mjs for the 500-LOC cap.
 */

/**
 * Join table: registry id → legacy feature label and per-feature
 * toggle key. The server sends these with every entry (``legacy_label``,
 * ``toggle_key``, ``method_value`` — a plugin entry's included); this
 * table answers only for an entry that arrived without them. It mirrors
 * ``_BUILTIN_FEATURES`` in bpp/web/picker_fields.py and a test keeps the
 * two identical.
 */
export const LEGACY_FEATURE_MAP = {
  sface_yunet: { legacy_label: "Face recognition", toggle_key: null },
  dlib_face_recognition_resnet_v1: { legacy_label: "Face recognition", toggle_key: null },
  insightface_buffalo_s: { legacy_label: null, toggle_key: null },
  insightface_scrfd_25g: { legacy_label: "SCRFD face detection", toggle_key: "model_scrfd" },
  opencv_yunet: { legacy_label: "Face detection", toggle_key: null },
  mediapipe_blazeface_short_range: { legacy_label: "Face detection", toggle_key: null },
  mediapipe_blazeface_full_range: { legacy_label: "BlazeFace full-range", toggle_key: "model_blazeface_fr" },
  mediapipe_face_landmarker: { legacy_label: "Expression scoring", toggle_key: "model_face_landmarker" },
  mediapipe_hand_landmarker: { legacy_label: "Hand suppression", toggle_key: "model_hand_landmarker" },
  mediapipe_pose_landmarker_lite: { legacy_label: "Pose estimation", toggle_key: "model_pose" },
  mediapipe_selfie_segmenter: { legacy_label: "Subject segmentation", toggle_key: "model_segmentation" },
  openai_clip_vit_b32_onnx: { legacy_label: "Smart search & dedup", toggle_key: null },
  ultralytics_yolov11n_pets: { legacy_label: "Pet detection", toggle_key: null },
  nudenet_320n: { legacy_label: "Content filter", toggle_key: null },
  lama_inpaint_research: { legacy_label: "AI object removal", toggle_key: null },
};

/** registry id → the ``face_embedding_method`` value that picks it (fallback). */
export const FACE_EMBEDDER_METHOD_VALUE = {
  sface_yunet: "sface",
  dlib_face_recognition_resnet_v1: "dlib",
  insightface_buffalo_s: "buffalo_s",
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
