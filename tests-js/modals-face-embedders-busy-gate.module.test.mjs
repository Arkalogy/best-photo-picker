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
  redownloadFaceEmbedderEntry,
  ensureCatalogWeights,
  uninstallFaceEmbedderEntry,
  _feOverflowToggle,
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

// ── Busy gate ───────────────────────────────────────────────────────
//
// While an action is in flight on a row (download, ensure-weights,
// uninstall), the menu trigger must not open a fresh menu and other
// action handlers must refuse to fire — otherwise a mid-download
// Uninstall could delete the files being written, or a stale menu
// click could double-trigger a network call.

describe("busy gate", () => {
  test("menu trigger refuses to open a busy row", async () => {
    document.body.innerHTML = `
      <table>
        <tr data-entry-id="opencv_yunet">
          <td class="fe-cell-status"></td>
          <td><div class="fe-overflow-menu" id="fe-ovr-opencv_yunet"></div></td>
        </tr>
      </table>
      <div id="toast-container"></div>
    `;
    _seedFaceEmbedderStateForTests({
      installState: {
        opencv_yunet: {
          status: "ready",
          files: [{ name: "yunet.onnx", exists: true }],
        },
      },
    });

    // Mock fetch to never resolve so the download stays in-flight.
    apiFetchMock.mockClear();
    /** @type {(value?: any) => void} */
    let resolveFetch = () => {};
    apiFetchMock.mockImplementation(() => new Promise((r) => (resolveFetch = r)));

    // Start a Redownload (sets busy state via _setRowStatusBusy).
    const redlPromise = redownloadFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    // Yield so the await apiFetch chain inside redownload sets busy.
    await Promise.resolve();

    // Now try to open the menu — should be refused with a toast.
    _feOverflowToggle("opencv_yunet");
    const menu = /** @type {HTMLElement} */ (document.getElementById("fe-ovr-opencv_yunet"));
    expect(menu.classList.contains("open"), "menu must NOT open while busy").toBe(false);
    const toasts = Array.from(document.querySelectorAll("#toast-container .toast"));
    const busyToast = toasts.find((t) => /busy|operation/i.test(t.textContent || ""));
    expect(busyToast, "a busy-refusal toast must surface").toBeDefined();

    // Clean up: resolve the pending fetch so the test doesn't hang.
    resolveFetch({});
    await redlPromise.catch(() => {});
  });

  test("a second redownloadFaceEmbedderEntry call refuses while first is in flight", async () => {
    _seedFaceEmbedderStateForTests({
      installState: {
        opencv_yunet: {
          status: "ready",
          files: [{ name: "yunet.onnx", exists: true }],
        },
      },
    });
    apiFetchMock.mockClear();
    /** @type {(value?: any) => void} */
    let resolveFetch = () => {};
    apiFetchMock.mockImplementation(() => new Promise((r) => (resolveFetch = r)));

    const first = redownloadFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    await Promise.resolve();

    // Second call should be no-op (no extra apiFetch beyond the first one's).
    const callCountBefore = apiFetchMock.mock.calls.length;
    await redownloadFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    expect(apiFetchMock.mock.calls.length).toBe(callCountBefore);

    const toasts = Array.from(document.querySelectorAll("#toast-container .toast"));
    const busyToast = toasts.find((t) => /busy|operation/i.test(t.textContent || ""));
    expect(busyToast, "a busy-refusal toast must surface").toBeDefined();

    resolveFetch({});
    await first.catch(() => {});
  });

  test("uninstall refuses to fire while a download is in flight on the same row", async () => {
    _seedFaceEmbedderStateForTests({
      installState: {
        opencv_yunet: {
          status: "ready",
          files: [{ name: "yunet.onnx", exists: true }],
        },
      },
    });
    apiFetchMock.mockClear();
    /** @type {(value?: any) => void} */
    let resolveFetch = () => {};
    apiFetchMock.mockImplementation(() => new Promise((r) => (resolveFetch = r)));

    const dl = redownloadFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    await Promise.resolve();

    // Uninstall during download should refuse — without this, files
    // would be deleted mid-write.
    const callCountBefore = apiFetchMock.mock.calls.length;
    await uninstallFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    expect(apiFetchMock.mock.calls.length).toBe(callCountBefore);

    resolveFetch({});
    await dl.catch(() => {});
  });

  test("ensureCatalogWeights refuses if the catalog row is busy", async () => {
    apiFetchMock.mockClear();
    /** @type {(value?: any) => void} */
    let resolveFetch = () => {};
    apiFetchMock.mockImplementation(() => new Promise((r) => (resolveFetch = r)));

    const first = ensureCatalogWeights("insightface_buffalo_s", "buffalo_s");
    await Promise.resolve();

    const callCountBefore = apiFetchMock.mock.calls.length;
    await ensureCatalogWeights("insightface_buffalo_s", "buffalo_s");
    expect(apiFetchMock.mock.calls.length).toBe(callCountBefore);

    resolveFetch({});
    await first.catch(() => {});
  });
});

describe("catalog entries with a fileless legacy record (LaMa / NudeNet)", () => {
  // Regression for the dead-end screenshots: LaMa / NudeNet are catalog
  // entries (weights fetched on demand) that ALSO carry a legacy feature
  // row with an empty files list. The old `isCatalog = !install`
  // inference was false for them, so the menu dropped Download entirely
  // and the row could never register as on-disk.

  /** A restricted, license-accepted inpainter the backend flags as catalog. */
  const lamaEntry = (overrides = {}) => ({
    id: "lama_inpaint_research",
    display_name: "LaMa inpainting (research weights, non-commercial)",
    kind: "inpainter",
    requires_explicit_ack: true,
    is_catalog_entry: true,
    expected_download_size_bytes: 205_803_670,
    catalog_on_disk: false,
    ...overrides,
  });

  /** The fileless legacy feature row the picker joins in (status="ready"
   *  just means the pip runtime is importable, not that weights exist). */
  const filelessLegacyRecord = { status: "ready", files: [] };

  test("not downloaded → Download is present AND enabled (no dead-end)", () => {
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["lama_inpaint_research"],
      acceptedIds: ["lama_inpaint_research"],
      catalogEntryIds: ["lama_inpaint_research"],
    });
    const entry = lamaEntry();
    const menu = _buildActionMenu(entry, filelessLegacyRecord);

    const download = findItem(menu, /^Download/);
    expect(download, "Download action must exist — this was the dead-end").toBeDefined();
    expect(download.enabled).toBe(true);
    expect(download.action).toBe("ensureCatalogWeights");
  });

  test("not-yet-accepted → Download present but disabled with 'accept first'", () => {
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["lama_inpaint_research"],
      acceptedIds: [],
      catalogEntryIds: ["lama_inpaint_research"],
    });
    const menu = _buildActionMenu(lamaEntry(), filelessLegacyRecord);
    const download = findItem(menu, /^Download/);
    expect(download).toBeDefined();
    expect(download.enabled).toBe(false);
    expect(download.sub).toMatch(/accept the license first/i);
  });

  test("catalog_on_disk is honoured even with a legacy record attached", () => {
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["lama_inpaint_research"],
      acceptedIds: ["lama_inpaint_research"],
      catalogEntryIds: ["lama_inpaint_research"],
    });
    // catalog_on_disk=true with a fileless legacy record: the old
    // `filesOnDisk || (isCatalog && catalogOnDisk)` ignored catalogOnDisk
    // here (isCatalog was false), leaving the row stuck "Not downloaded".
    const state = _deriveRowState(lamaEntry({ catalog_on_disk: true }), filelessLegacyRecord);
    expect(state.onDisk).toBe(true);
    expect(state.lifecycle).not.toBe(ROW_LIFECYCLE.NOT_DOWNLOADED);

    const menu = _buildActionMenu(lamaEntry({ catalog_on_disk: true }), filelessLegacyRecord);
    expect(findItem(menu, /^Uninstall/).enabled).toBe(true);
  });

  test("uninstall routes to the catalog endpoint, not 'not installed'", async () => {
    // Reset the implementation, not just call history — earlier tests
    // swap apiFetch for a manually-controlled (never-resolving) promise.
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({});
    const { appConfirm } = await import("../bpp/web/static/js/modules/dialogs.mjs");
    /** @type {any} */ (appConfirm).mockResolvedValue(true);
    _seedFaceEmbedderStateForTests({
      catalogEntryIds: ["lama_inpaint_research"],
      installState: { lama_inpaint_research: filelessLegacyRecord },
    });
    await uninstallFaceEmbedderEntry("lama_inpaint_research", "LaMa inpainting");
    const calledUrls = apiFetchMock.mock.calls.map((c) => c[0]);
    expect(calledUrls).toContain("/api/v1/face-embedders/uninstall-weights");
    // The legacy per-file uninstall must NOT be used for a catalog entry.
    expect(calledUrls).not.toContain("/api/v1/models/uninstall");
  });
});
