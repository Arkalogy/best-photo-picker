// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn().mockResolvedValue({});

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const {
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  _deriveRowState,
  ROW_LIFECYCLE,
} = await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");

beforeEach(() => {
  document.body.innerHTML = `
    <div id="face-embedder-picker"></div>
    <div id="toast-container"></div>
  `;
  _resetFaceEmbedderStateForTests();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Lifecycle truth table ─────────────────────────────────────────
//
// _deriveRowState returns exactly one lifecycle value per row. These
// tests pin the (picked, on-disk, license-accepted, install-state)
// inputs to the expected lifecycle. Status cell + menu + size cell
// all derive from this, so getting the lifecycle right makes the
// downstream rendering correct by construction.

describe("_deriveRowState — lifecycle is exactly one of the 8 enum values", () => {
  /**
   * @param {string} id
   * @param {Record<string, any>} [extra]
   */
  const faceEmbedder = (id, extra = {}) => ({
    id,
    display_name: id,
    kind: "face_embedder",
    ...extra,
  });

  // Inputs: { picked: boolean, install, restricted: bool, accepted: bool,
  //           catalogOnDisk?: bool }
  // Expected: ROW_LIFECYCLE.*
  const cases = [
    // Picked + on disk + license clear → RUNNING
    {
      name: "picked + installable on disk + permissive → RUNNING",
      picked: true,
      entry: faceEmbedder("sface_yunet"),
      install: { status: "ready", files: [{ name: "sface.onnx", exists: true }] },
      expected: "running",
    },
    {
      name: "picked + catalog on disk + accepted → RUNNING",
      picked: true,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
        catalog_on_disk: true,
      }),
      install: undefined,
      accepted: true,
      expected: "running",
    },

    // Picked + weights missing → PICKED_NEEDS_DOWNLOAD
    {
      name: "picked + installable missing weights → PICKED_NEEDS_DOWNLOAD",
      picked: true,
      entry: faceEmbedder("dlib_face_recognition_resnet_v1"),
      install: { status: "missing", files: [{ name: "dlib.dat", exists: false }] },
      expected: "picked-needs-download",
    },
    {
      name: "picked + catalog missing → PICKED_NEEDS_DOWNLOAD",
      picked: true,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
        catalog_on_disk: false,
      }),
      install: undefined,
      accepted: true,
      expected: "picked-needs-download",
    },

    // Picked + on disk + license blocked → PICKED_NEEDS_LICENSE
    // (License gate takes priority over the download gate.)
    {
      name: "picked + on disk + license revoked → PICKED_NEEDS_LICENSE",
      picked: true,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
        catalog_on_disk: true,
      }),
      install: undefined,
      accepted: false,
      expected: "picked-needs-license",
    },

    // Not picked + on disk + license blocked → NEEDS_LICENSE
    {
      name: "not picked + catalog on disk + license missing → NEEDS_LICENSE",
      picked: false,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
        catalog_on_disk: true,
      }),
      install: undefined,
      accepted: false,
      expected: "needs-license",
    },
    {
      name: "not picked + installable ready + license missing → NEEDS_LICENSE",
      picked: false,
      entry: {
        id: "nudenet_320n",
        display_name: "NudeNet",
        kind: "nudity_classifier",
        requires_explicit_ack: true,
      },
      install: { status: "ready", files: [{ name: "nudenet.onnx", exists: true }] },
      accepted: false,
      expected: "needs-license",
    },

    // Not picked + on disk + license clear → READY
    {
      name: "not picked + installable on disk + permissive → READY",
      picked: false,
      entry: faceEmbedder("dlib_face_recognition_resnet_v1"),
      install: { status: "ready", files: [{ name: "dlib.dat", exists: true }] },
      expected: "ready",
    },
    {
      name: "not picked + catalog on disk + accepted → READY",
      picked: false,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
        catalog_on_disk: true,
      }),
      install: undefined,
      accepted: true,
      expected: "ready",
    },

    // install.status === "no_library" → NEEDS_RUNTIME
    {
      name: "no_library install status → NEEDS_RUNTIME",
      picked: false,
      entry: faceEmbedder("any_id"),
      install: { status: "no_library", files: [], install_hint: "pip install foo" },
      expected: "needs-runtime",
    },

    // install.status === "partial" → PARTIAL
    {
      name: "partial install status → PARTIAL",
      picked: false,
      entry: faceEmbedder("any_id"),
      install: {
        status: "partial",
        files: [
          { name: "a.onnx", exists: true },
          { name: "b.onnx", exists: false },
        ],
      },
      expected: "partial",
    },

    // Nothing on disk, no install state, no catalog flag → NOT_DOWNLOADED
    {
      name: "no install + no catalog flag → NOT_DOWNLOADED",
      picked: false,
      entry: faceEmbedder("insightface_buffalo_s", {
        requires_explicit_ack: true,
      }),
      install: undefined,
      expected: "not-downloaded",
    },
    {
      name: "install missing + no files on disk → NOT_DOWNLOADED",
      picked: false,
      entry: faceEmbedder("sface_yunet"),
      install: { status: "missing", files: [{ name: "sface.onnx", exists: false }] },
      expected: "not-downloaded",
    },
  ];

  for (const c of cases) {
    test(c.name, () => {
      // Pick by mapping the entry id to its face_embedding_method via
      // _FACE_EMBEDDER_METHOD_VALUE (sface_yunet → "sface", etc.).
      const methodMap = {
        sface_yunet: "sface",
        dlib_face_recognition_resnet_v1: "dlib",
        insightface_buffalo_s: "buffalo_s",
      };
      /** @type {{activeFaceMethod: string, requiresAckIds?: string[], acceptedIds?: string[]}} */
      const seed = {
        activeFaceMethod: c.picked ? methodMap[c.entry.id] || "sface" : "sface",
      };
      if (/** @type {any} */ (c.entry).requires_explicit_ack) {
        seed.requiresAckIds = [c.entry.id];
        if (c.accepted) seed.acceptedIds = [c.entry.id];
      }
      // When picked is true but the entry isn't sface_yunet, seed
      // activeFaceMethod to that entry's method. When picked is false,
      // sface is the default so seed accordingly.
      if (c.picked && methodMap[c.entry.id]) {
        seed.activeFaceMethod = methodMap[c.entry.id];
      } else if (!c.picked && c.entry.id === "sface_yunet") {
        seed.activeFaceMethod = "dlib"; // anything but sface
      }
      _seedFaceEmbedderStateForTests(seed);
      const state = _deriveRowState(c.entry, c.install);
      expect(state.lifecycle).toBe(c.expected);
    });
  }
});

describe("_deriveRowState — supporting models", () => {
  test("a model that runs alongside the default reads Running, not Backup", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["mediapipe_blazeface_full_range"],
      acceptedIds: ["mediapipe_blazeface_full_range"],
    });
    const state = _deriveRowState(
      {
        id: "mediapipe_blazeface_full_range",
        kind: "face_detector",
        requires_explicit_ack: true,
        default_for_kind: false,
        runs_alongside_default: true,
      },
      { status: "ready", files: [{ name: "BlazeFace full-range", exists: true }] }
    );
    expect(state.lifecycle).toBe("running");
  });

  test("weights shipped inside a library count as on disk", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["opencv_haar_frontalface"],
      acceptedIds: ["opencv_haar_frontalface"],
    });
    const state = _deriveRowState(
      {
        id: "opencv_haar_frontalface",
        kind: "face_detector",
        requires_explicit_ack: true,
        default_for_kind: false,
        ships_with_runtime: true,
      },
      undefined
    );
    expect(state.onDisk).toBe(true);
    expect(state.lifecycle).toBe("backup-available");
  });
});
