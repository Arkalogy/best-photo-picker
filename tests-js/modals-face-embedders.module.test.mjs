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
  _renderRowForTests,
  openFaceEmbedderAcceptance,
  redownloadFaceEmbedderEntry,
  setActiveFaceEmbedder,
  ensureCatalogWeights,
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

const lastToast = () =>
  /** @type {HTMLElement | null} */ (document.querySelector("#toast-container .toast"));

const stubApiFetch = () => {
  apiFetchMock.mockClear();
  apiFetchMock.mockResolvedValue({});
  return apiFetchMock;
};

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

describe("setActiveFaceEmbedder — restricted-model pre-gate", () => {
  test("restricted entry WITHOUT acceptance: toasts and refuses to PUT settings", async () => {
    const apiFetch = stubApiFetch();
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: [],
    });

    await setActiveFaceEmbedder("buffalo_s", "InsightFace buffalo_s", "insightface_buffalo_s");

    expect(apiFetch).not.toHaveBeenCalled();
    const t = lastToast();
    expect(t).not.toBeNull();
    expect(t?.textContent || "").toMatch(/acceptance|review|accept/i);
  });

  test("restricted entry WITH acceptance: proceeds to PUT settings", async () => {
    const apiFetch = stubApiFetch();
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
    });

    await setActiveFaceEmbedder("buffalo_s", "InsightFace buffalo_s", "insightface_buffalo_s");

    const settingsCall = apiFetch.mock.calls.find((c) => c[0] === "/api/v1/settings");
    expect(settingsCall).toBeDefined();
    expect(settingsCall[1].method).toBe("PUT");
    expect(JSON.parse(settingsCall[1].body)).toEqual({
      face_embedding_method: "buffalo_s",
    });
  });

  test("permissive (non-restricted) entry: proceeds without checking acceptances", async () => {
    const apiFetch = stubApiFetch();
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: [],
    });

    await setActiveFaceEmbedder("sface", "SFace", "sface_yunet");

    const settingsCall = apiFetch.mock.calls.find((c) => c[0] === "/api/v1/settings");
    expect(settingsCall).toBeDefined();
    expect(settingsCall[1].method).toBe("PUT");
  });

  test("empty registry id: proceeds (no ack required when unknown)", async () => {
    // If the picker hasn't populated the requires-ack set yet (e.g.
    // a stale page calls setActiveFaceEmbedder before
    // loadFaceEmbedderPicker has returned), an empty / unknown id
    // can't be in the restricted set, so we let it through. The
    // runtime gate on the backend catches the actual load if the
    // model is restricted.
    const apiFetch = stubApiFetch();
    _seedFaceEmbedderStateForTests({
      requiresAckIds: [],
      acceptedIds: [],
    });

    await setActiveFaceEmbedder("buffalo_s", "Mystery model", "");

    const settingsCall = apiFetch.mock.calls.find((c) => c[0] === "/api/v1/settings");
    expect(settingsCall).toBeDefined();
  });
});

describe("_buildActionMenu — full lifecycle, greyed when unavailable/done", () => {
  const detector = { id: "opencv_yunet", display_name: "OpenCV YuNet", kind: "face_detector" };

  test("permissive detector, missing weights → Download enabled, Uninstall greyed", () => {
    const menu = _buildActionMenu(
      detector,
      { status: "missing", files: [{ name: "yunet.onnx", exists: false }] },
      false,
      false,
      undefined
    );
    const dl = findItem(menu, /^Download$/);
    expect(dl).toBeDefined();
    expect(dl.enabled).toBe(true);
    expect(dl.action).toBe("redownloadFaceEmbedderEntry");
    const un = findItem(menu, /Uninstall/);
    expect(un.enabled).toBe(false); // nothing on disk
    expect(un.sub).toMatch(/not installed/i);
  });

  test("restricted on-disk model + license NOT accepted → Redownload disabled (YOLO bug)", () => {
    // The bug screenshot: YOLO pets weights pre-existed on disk from
    // before the legal posture landed. The user clicked Redownload,
    // the picker fired the network call (Redownload was wrongly
    // enabled), and the server-side enforce_load_policy_for gate
    // refused with a long "blocked_needs_ack" toast. The picker must
    // gate Redownload the same way it gates Download — license first.
    const restrictedEntry = {
      id: "ultralytics_yolov11n_pets",
      display_name: "YOLOv11n (pet detection)",
      kind: "pet_detector",
      requires_explicit_ack: true,
    };
    _seedFaceEmbedderStateForTests({
      requiresAckIds: [restrictedEntry.id],
      acceptedIds: [], // license NOT yet accepted
    });
    const menu = _buildActionMenu(
      restrictedEntry,
      { status: "ready", files: [{ name: "yolo.onnx", exists: true }] },
      false,
      false,
      undefined
    );
    const redl = findItem(menu, /^Redownload$/);
    expect(redl, "Redownload must surface so the user sees the goal").toBeDefined();
    expect(redl.enabled, "Redownload must be disabled until acceptance").toBe(false);
    expect(redl.sub).toMatch(/accept the license first/i);
  });

  test("restricted on-disk model + license ACCEPTED → Redownload enabled (regression guard)", () => {
    const restrictedEntry = {
      id: "ultralytics_yolov11n_pets",
      display_name: "YOLOv11n (pet detection)",
      kind: "pet_detector",
      requires_explicit_ack: true,
    };
    _seedFaceEmbedderStateForTests({
      requiresAckIds: [restrictedEntry.id],
      acceptedIds: [restrictedEntry.id],
    });
    const menu = _buildActionMenu(
      restrictedEntry,
      { status: "ready", files: [{ name: "yolo.onnx", exists: true }] },
      false,
      false,
      undefined
    );
    expect(findItem(menu, /^Redownload$/).enabled).toBe(true);
  });

  test("ready detector → Redownload + Uninstall both enabled", () => {
    const menu = _buildActionMenu(
      detector,
      { status: "ready", files: [{ name: "yunet.onnx", exists: true }] },
      false,
      false,
      undefined
    );
    expect(findItem(menu, /Redownload/).enabled).toBe(true);
    expect(findItem(menu, /Uninstall/).enabled).toBe(true);
  });

  describe("restricted face embedder (buffalo_s)", () => {
    const entry = {
      id: "insightface_buffalo_s",
      display_name: "InsightFace buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      license_summary: "InsightFace research-only license.",
    };

    test("NOT accepted → Review opens the license modal; Download + Use both greyed with reason", () => {
      _seedFaceEmbedderStateForTests({ requiresAckIds: [entry.id], acceptedIds: [] });
      const menu = _buildActionMenu(entry, undefined, false, true, "buffalo_s");

      const review = findItem(menu, /Review/);
      expect(review.enabled, "review must be clickable").toBe(true);
      expect(review.action).toBe("openFaceEmbedderAcceptance");
      // The license TEXT lives in the modal, not the menu — keep the menu
      // a one-liner (no wall-of-text sub).
      expect(review.sub, "no license paragraph crammed into the menu").toBeFalsy();

      const dl = findItem(menu, /^Download/);
      expect(dl, "Download must surface for catalog entries").toBeDefined();
      expect(dl.enabled).toBe(false);
      expect(dl.sub).toMatch(/accept the license first/i);

      const use = findItem(menu, /Use this model/);
      expect(use.enabled).toBe(false);
      expect(use.sub).toMatch(/accept the license first/i);
    });

    test("accepted, NOT yet on disk → Download surfaces with size; Use greyed with 'Download it first'", () => {
      // Bug 2 fix: a catalog entry that lives only in the registry
      // must NOT let the user activate it. Clicking Use on a missing
      // catalog model would silently trigger the multi-MB fetch at
      // first analyze — exactly what the "Nothing should be silent"
      // rule forbids. The menu now offers an explicit Download step
      // with the expected size up front.
      _seedFaceEmbedderStateForTests({ requiresAckIds: [entry.id], acceptedIds: [entry.id] });
      const menu = _buildActionMenu(
        {
          ...entry,
          expected_download_size_bytes: 127_596_032, // ~121.7 MB
          catalog_on_disk: false,
        },
        undefined,
        false,
        true,
        "buffalo_s"
      );

      const dl = findItem(menu, /^Download/);
      expect(dl, "Catalog entries must surface an explicit Download step").toBeDefined();
      expect(dl.enabled).toBe(true);
      expect(dl.action).toBe("ensureCatalogWeights");
      // The label carries the size — the user knows what they're committing to.
      expect(dl.label).toMatch(/121\.7\s*MB/);

      const use = findItem(menu, /Use this model/);
      expect(use.enabled, "Use must be disabled until weights are on disk").toBe(false);
      expect(use.sub).toMatch(/download it first/i);
    });

    test("accepted, ON disk → Use enabled; Redownload surfaces (not Download)", () => {
      _seedFaceEmbedderStateForTests({
        requiresAckIds: [entry.id],
        acceptedIds: [entry.id],
        activeFaceMethod: "sface",
      });
      const menu = _buildActionMenu(
        { ...entry, catalog_on_disk: true },
        undefined,
        false,
        true,
        "buffalo_s"
      );

      // Catalog entry that is on disk should mirror an installed model: the
      // Download slot becomes Redownload, and Use is finally enabled.
      const redl = findItem(menu, /^Redownload$/);
      expect(redl, "On-disk catalog entry shows Redownload, not Download").toBeDefined();
      expect(redl.action).toBe("ensureCatalogWeights");

      const use = findItem(menu, /Use this model/);
      expect(use.enabled, "Use enabled once weights are on disk").toBe(true);
      expect(use.sub).toBeFalsy();

      // Uninstall is available — the menu now offers the same
      // Review → Download → Use → Uninstall lifecycle as installable
      // entries.
      const uninstall = findItem(menu, /Uninstall/);
      expect(uninstall.enabled).toBe(true);
    });

    test("REGRESSION: accepted + on disk → Review STILL present and clickable (terms never hidden)", () => {
      _seedFaceEmbedderStateForTests({
        requiresAckIds: [entry.id],
        acceptedIds: [entry.id],
        activeFaceMethod: "sface",
      });
      const menu = _buildActionMenu(
        { ...entry, catalog_on_disk: true },
        undefined,
        false,
        true,
        "buffalo_s"
      );

      // After acceptance the item states the fact but stays clickable
      // (find by "license" — the label flips to "Model's license accepted").
      const review = findItem(menu, /license/i);
      expect(review, "license item must never disappear").toBeDefined();
      expect(review.enabled, "license item must stay clickable after acceptance").toBe(true);
      expect(review.done).toBe(true);
      expect(review.label).toBe("Model’s license accepted");
    });

    test("active → Use shows as completed 'In use'", () => {
      _seedFaceEmbedderStateForTests({
        requiresAckIds: [entry.id],
        acceptedIds: [entry.id],
        activeFaceMethod: "buffalo_s",
      });
      const menu = _buildActionMenu(
        { ...entry, catalog_on_disk: true },
        undefined,
        true,
        true,
        "buffalo_s"
      );
      const inUse = findItem(menu, /In use/);
      expect(inUse).toBeDefined();
      expect(inUse.done).toBe(true);
      expect(inUse.enabled).toBe(false);
    });
  });

  describe("Bug 1: installable face embedder, weights NOT on disk", () => {
    // For an entry with download wiring but no files on disk yet,
    // Use this model must be greyed with a "Download it first" sub.
    // dlib is the non-picked face embedder (default active is sface),
    // so these tests pin the "not picked + missing/ready" branches.
    const entry = {
      id: "dlib_face_recognition_resnet_v1",
      display_name: "dlib",
      kind: "face_embedder",
      requires_explicit_ack: false,
    };

    test("missing files → Download enabled; Use disabled with 'Download it first'", () => {
      _seedFaceEmbedderStateForTests({ activeFaceMethod: "sface" });
      const menu = _buildActionMenu(entry, {
        status: "missing",
        files: [{ name: "dlib.dat", exists: false }],
      });

      const dl = findItem(menu, /^Download$/);
      expect(dl.enabled).toBe(true);

      const use = findItem(menu, /Use this model/);
      expect(use.enabled, "Use must be disabled until the weights are on disk").toBe(false);
      expect(use.sub).toMatch(/download it first/i);
    });

    test("files on disk → Use enabled", () => {
      _seedFaceEmbedderStateForTests({ activeFaceMethod: "sface" });
      const menu = _buildActionMenu(entry, {
        status: "ready",
        files: [{ name: "dlib.dat", exists: true }],
      });
      const use = findItem(menu, /Use this model/);
      expect(use.enabled).toBe(true);
      expect(use.sub).toBeFalsy();
    });
  });
});

describe("_renderRowForTests — full row integrates the action menu", () => {
  test("REGRESSION: uninstalled (missing) detector renders a ⋯ menu, not an empty cell", () => {
    _seedFaceEmbedderStateForTests({
      installState: {
        opencv_yunet: { status: "missing", files: [{ name: "yunet.onnx", exists: false }] },
      },
    });
    const html = _renderRowForTests({
      id: "opencv_yunet",
      display_name: "OpenCV YuNet (face detection)",
      kind: "face_detector",
    });
    expect(html).toContain('data-action="_feOverflowToggle"');
    expect(html).not.toContain("fe-actions-empty");
    expect(html).toContain("Download");
  });

  test("REGRESSION: accepted restricted catalog model still renders a clickable Review item", () => {
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["insightface_buffalo_s"],
      acceptedIds: ["insightface_buffalo_s"],
      activeFaceMethod: "sface",
    });
    const html = _renderRowForTests({
      id: "insightface_buffalo_s",
      display_name: "InsightFace buffalo_s",
      kind: "face_embedder",
      requires_explicit_ack: true,
      license_summary: "InsightFace research-only license.",
    });
    // Review action present AND not disabled (the enabled button carries
    // data-action; a disabled one would not).
    expect(html).toMatch(/data-action="openFaceEmbedderAcceptance"/);
    expect(html).toContain('data-action="_feOverflowToggle"');
  });

  test("status + license cells carry their right-align hook classes", () => {
    _resetFaceEmbedderStateForTests();
    const html = _renderRowForTests({
      id: "x",
      display_name: "X",
      kind: "face_detector",
    });
    expect(html).toContain('class="fe-cell-status"');
    expect(html).toContain('class="fe-cell-license"');
    expect(html).toContain('class="fe-cell-size"');
  });

  test("status label has no coloured dot (noise removed)", () => {
    _seedFaceEmbedderStateForTests({
      installState: { x: { status: "ready", files: [{ name: "a", exists: true }] } },
    });
    const html = _renderRowForTests({
      id: "x",
      display_name: "X",
      kind: "face_detector",
      primary_for_kind: true,
    });
    expect(html).not.toContain("fe-dot");
    // default_for_kind detector + on disk → Running, not Ready
    expect(html).toContain("Running");
  });
});
