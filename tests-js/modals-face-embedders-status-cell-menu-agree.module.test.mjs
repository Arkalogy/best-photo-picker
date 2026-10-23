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
  _buildActionMenu,
  _deriveRowState,
  ROW_LIFECYCLE,
  _renderRowForTests,
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

// ── Lifecycle action menu (_buildActionMenu) ──
//
// The menu shows the FULL lifecycle (review → download → use → uninstall)
// every time and greys out steps that aren't available yet or are done, so
// the user always sees where they are. Two hard rules:
//   * "Review … license" is ALWAYS present and clickable for a restricted
//     model — even after acceptance (hiding it was the regression).
//   * A step the user can't do yet is shown DISABLED with a reason, never
//     a silently missing control.

/** Find a menu item whose label matches `re`. */
const findItem = (menu, re) => menu.find((it) => re.test(it.label));

describe("status cell + menu agree (via shared lifecycle)", () => {
  // Single integration test per lifecycle value: seed the inputs that
  // produce that lifecycle, render the row, and check the visible
  // status text. The cell-text mapping lives in one table; this test
  // just verifies the table is plumbed in.

  test("RUNNING → status reads 'Running' (green) + 'In use ✓' in menu", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      installState: {
        sface_yunet: { status: "ready", files: [{ name: "sface.onnx", exists: true }] },
      },
    });
    const html = _renderRowForTests({
      id: "sface_yunet",
      display_name: "SFace",
      kind: "face_embedder",
    });
    expect(html).toMatch(/>Running</);
    expect(html).toContain("fe-status-active");
    const menu = _buildActionMenu(
      { id: "sface_yunet", display_name: "SFace", kind: "face_embedder" },
      { status: "ready", files: [{ name: "sface.onnx", exists: true }] }
    );
    expect(findItem(menu, /^In use$/)).toBeDefined();
  });

  test("PICKED_NEEDS_DOWNLOAD → 'Can't run' / 'needs download' + Use disabled", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "dlib",
      installState: {
        dlib_face_recognition_resnet_v1: {
          status: "missing",
          files: [{ name: "dlib.dat", exists: false }],
        },
      },
    });
    const html = _renderRowForTests({
      id: "dlib_face_recognition_resnet_v1",
      display_name: "dlib",
      kind: "face_embedder",
    });
    expect(html).toMatch(/Can\W*t run/);
    expect(html).toContain("needs download");
    expect(html).toContain("fe-status-warn");

    const menu = _buildActionMenu(
      { id: "dlib_face_recognition_resnet_v1", display_name: "dlib", kind: "face_embedder" },
      { status: "missing", files: [{ name: "dlib.dat", exists: false }] }
    );
    expect(findItem(menu, /^In use$/)).toBeUndefined();
    expect(findItem(menu, /Use this model/).enabled).toBe(false);
    expect(findItem(menu, /Use this model/).sub).toMatch(/download to activate/i);
    expect(findItem(menu, /^Download$/).enabled).toBe(true);
  });

  test("PICKED_NEEDS_LICENSE → 'Can't run' / 'needs license' + Use disabled", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "buffalo_s",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: [], // post-revocation
    });
    const entry = {
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      catalog_on_disk: true,
    };
    const html = _renderRowForTests(entry);
    expect(html).toMatch(/Can\W*t run/);
    expect(html).toContain("needs license");
    expect(html).not.toContain("needs download");

    const menu = _buildActionMenu(entry, undefined);
    expect(findItem(menu, /Use this model/).enabled).toBe(false);
    expect(findItem(menu, /Use this model/).sub).toMatch(/accept the license first/i);
  });

  test("NEEDS_LICENSE → 'Needs license' (orange) + Use AND Redownload both disabled", () => {
    // The bug screenshot pair: status said "Ready" while menu said
    // "Accept the license first" on Use and Redownload. New row
    // state collapses both halves to NEEDS_LICENSE → consistent.
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: [],
    });
    const entry = {
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      catalog_on_disk: true,
    };
    const html = _renderRowForTests(entry);
    expect(html).toMatch(/>Needs license</);
    expect(html).toContain("fe-status-warn");
    expect(html).not.toMatch(/>Ready</);

    const menu = _buildActionMenu(entry, undefined);
    expect(findItem(menu, /Use this model/).enabled).toBe(false);
    expect(findItem(menu, /Use this model/).sub).toMatch(/accept the license first/i);
    // Redownload — the YOLO bug — must also be gated by license.
    const redl = findItem(menu, /^Redownload$/);
    expect(redl).toBeDefined();
    expect(redl.enabled).toBe(false);
    expect(redl.sub).toMatch(/accept the license first/i);
  });

  test("READY → 'Ready' (muted) + Use enabled", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      installState: {
        dlib_face_recognition_resnet_v1: {
          status: "ready",
          files: [{ name: "dlib.dat", exists: true }],
        },
      },
    });
    const entry = {
      id: "dlib_face_recognition_resnet_v1",
      display_name: "dlib",
      kind: "face_embedder",
    };
    const html = _renderRowForTests(entry);
    expect(html).toMatch(/>Ready</);

    const menu = _buildActionMenu(entry, {
      status: "ready",
      files: [{ name: "dlib.dat", exists: true }],
    });
    expect(findItem(menu, /Use this model/).enabled).toBe(true);
  });

  test("NOT_DOWNLOADED catalog → 'Not downloaded' + Download enabled (when license clear)", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
    });
    const entry = {
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      catalog_on_disk: false,
      expected_download_size_bytes: 127_596_032,
    };
    const html = _renderRowForTests(entry);
    expect(html).toContain("Not downloaded");
    expect(html).toMatch(/~121\.7\s*MB/); // size estimate prefix

    const menu = _buildActionMenu(entry, undefined);
    expect(findItem(menu, /^Download/).enabled).toBe(true);
  });

  test("catalog size cell uses the normal size class when on disk (not italic estimate)", () => {
    // The italic .fe-cell-size-expected style is reserved for
    // pre-download estimates ("~121.7 MB"). Once weights are on
    // disk the size matches the installable rows visually — same
    // .fe-cell-size-val class, no italic.
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
    });
    const html = _renderRowForTests({
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      catalog_on_disk: true,
      expected_download_size_bytes: 127_596_032,
    });
    expect(html).toContain("fe-cell-size-val");
    expect(html).not.toContain("fe-cell-size-expected");
  });

  test("catalog size cell drops '~' once weights are on disk", () => {
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
    });
    const html = _renderRowForTests({
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      catalog_on_disk: true,
      expected_download_size_bytes: 127_596_032,
    });
    expect(html).toMatch(/>121\.7\s*MB</);
    expect(html).not.toMatch(/>~121\.7\s*MB</);
  });

  test("non-embedder DEFAULT row + on disk → RUNNING (not the misleading 'Ready')", () => {
    // Face detectors (and other non-embedder kinds) have no user-
    // pickable setting — the registry's default_for_kind flag names
    // the row the runtime uses. Before this rule, SCRFD showed
    // "Ready" while being the default face detector currently
    // running. Now it correctly reads "Running."
    _seedFaceEmbedderStateForTests({
      installState: {
        insightface_scrfd_25g: {
          status: "ready",
          files: [{ name: "scrfd.onnx", exists: true }],
        },
      },
    });
    const scrfd = {
      id: "insightface_scrfd_25g",
      display_name: "InsightFace SCRFD 2.5g",
      kind: "face_detector",
      primary_for_kind: true,
    };
    const state = _deriveRowState(scrfd, {
      status: "ready",
      files: [{ name: "scrfd.onnx", exists: true }],
    });
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.RUNNING);

    const html = _renderRowForTests(scrfd);
    expect(html).toMatch(/>Running</);
  });

  test("non-embedder NON-DEFAULT row + on disk → BACKUP_AVAILABLE (not pickable, runs only as auto-fallback)", () => {
    // YuNet is the face-detection fallback (not default_for_kind).
    // No user-pickable switch exists for face detectors; the runtime
    // uses YuNet only when SCRFD can't load. Reading this row as
    // "Ready" misled the user into looking for an activate action
    // that doesn't exist — BACKUP_AVAILABLE makes the situation
    // explicit (status "Backup" + sub "auto-fallback").
    _seedFaceEmbedderStateForTests({
      installState: {
        opencv_yunet: {
          status: "ready",
          files: [{ name: "yunet.onnx", exists: true }],
        },
      },
    });
    const yunet = {
      id: "opencv_yunet",
      display_name: "OpenCV YuNet",
      kind: "face_detector",
      default_for_kind: false,
    };
    const state = _deriveRowState(yunet, {
      status: "ready",
      files: [{ name: "yunet.onnx", exists: true }],
    });
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.BACKUP_AVAILABLE);

    const html = _renderRowForTests(yunet);
    expect(html).toMatch(/>Backup</);
    expect(html).toContain("auto-fallback");
    // The misleading old label is gone — the row no longer reads
    // "Ready" and the menu won't dangle a Use-this-model option.
    expect(html).not.toMatch(/>Ready</);
  });

  test("face_embedder NON-PICKED row + on disk → READY (still pickable via Use this model)", () => {
    // Regression guard: the BACKUP_AVAILABLE rule is non-embedder only.
    // A face_embedder alternative remains READY because the user CAN
    // switch to it via the face_embedding_method setting.
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "sface",
      installState: {
        dlib_face_recognition_resnet_v1: {
          status: "ready",
          files: [{ name: "dlib.dat", exists: true }],
        },
      },
    });
    const dlib = {
      id: "dlib_face_recognition_resnet_v1",
      display_name: "dlib",
      kind: "face_embedder",
    };
    const state = _deriveRowState(dlib, {
      status: "ready",
      files: [{ name: "dlib.dat", exists: true }],
    });
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.READY);
  });

  test("multi-row sanity: only ONE row can be RUNNING at once", () => {
    // The three-row screenshot bug: SFace, dlib, and buffalo_s all
    // showed Running simultaneously. With lifecycle, only the picked
    // row whose weights are on disk earns RUNNING; every other row
    // must land in a different lifecycle value.
    _seedFaceEmbedderStateForTests({
      activeFaceMethod: "buffalo_s",
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
      installState: {
        sface_yunet: { status: "ready", files: [{ name: "sface.onnx", exists: true }] },
        dlib_face_recognition_resnet_v1: {
          status: "ready",
          files: [{ name: "dlib.dat", exists: true }],
        },
      },
    });
    const sfaceState = _deriveRowState(
      { id: "sface_yunet", display_name: "SFace", kind: "face_embedder" },
      { status: "ready", files: [{ name: "sface.onnx", exists: true }] }
    );
    const dlibState = _deriveRowState(
      { id: "dlib_face_recognition_resnet_v1", display_name: "dlib", kind: "face_embedder" },
      { status: "ready", files: [{ name: "dlib.dat", exists: true }] }
    );
    const buffaloState = _deriveRowState(
      {
        id: "insightface_buffalo_s",
        display_name: "buffalo_s",
        kind: "face_embedder",
        requires_explicit_ack: true,
        catalog_on_disk: true,
      },
      undefined
    );
    // Buffalo is picked & on disk & accepted — only it is RUNNING.
    expect(buffaloState.lifecycle).toBe(ROW_LIFECYCLE.RUNNING);
    expect(sfaceState.lifecycle).toBe(ROW_LIFECYCLE.READY);
    expect(dlibState.lifecycle).toBe(ROW_LIFECYCLE.READY);
  });
});

// ── Status column right-alignment survives the inline-flex child ──
//
// The status label is a `display:inline-flex` span (dot + text). A cell
// `text-align:right` must still push that inline-level box to the right;
// this guards against a future change (e.g. making the cell `display:flex`
// without `justify-content`) silently reverting the alignment.
describe("status cell right-alignment (computed)", () => {
  test("text-align:right on .fe-cell-status resolves to right for an inline-flex child", () => {
    document.body.innerHTML = `
      <style>
        .fe-cell-status { text-align: right; }
        .fe-status { display: inline-flex; }
      </style>
      <table><tr>
        <td class="fe-cell-status"><span class="fe-status">Installed</span></td>
      </tr></table>`;
    const td = /** @type {HTMLElement} */ (document.querySelector(".fe-cell-status"));
    expect(getComputedStyle(td).textAlign).toBe("right");
  });
});
