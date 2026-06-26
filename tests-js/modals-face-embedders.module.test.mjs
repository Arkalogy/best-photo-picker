// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn().mockResolvedValue({});
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
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
  _acceptanceGateSatisfied,
  closeFaceEmbedderAcceptance,
  openFaceEmbedderAcceptance,
  redownloadFaceEmbedderEntry,
  revokeFaceEmbedderAcceptance,
  setActiveFaceEmbedder,
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

const lastToast = () =>
  /** @type {HTMLElement | null} */ (document.querySelector("#toast-container .toast"));

const stubApiFetch = () => {
  apiFetchMock.mockClear();
  apiFetchMock.mockResolvedValue({});
  return apiFetchMock;
};

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
      default_for_kind: true,
    });
    expect(html).not.toContain("fe-dot");
    // default_for_kind detector + on disk → Running, not Ready
    expect(html).toContain("Running");
  });
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
      default_for_kind: true,
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

// ── Download is NOT silent: in-row progress + real error reason ──
//
// The /redownload endpoint blocks until the file is fetched (seconds).
// Per the "nothing should be silent" rule, the row must show progress —
// not just a fire-and-forget toast — and a failure must surface the real
// reason, not a generic "failed".

describe("redownloadFaceEmbedderEntry — progress + error surfacing", () => {
  const seedRow = () => {
    _seedFaceEmbedderStateForTests({
      installState: {
        x: { status: "ready", label: "X model", files: [{ name: "x.onnx", exists: true }] },
      },
    });
    document.getElementById("face-embedder-picker").innerHTML = `
      <table><tbody>
        <tr data-entry-id="x">
          <td class="fe-cell-status"><span class="fe-status">Installed</span></td>
        </tr>
      </tbody></table>`;
  };
  const statusText = () =>
    document.querySelector('tr[data-entry-id="x"] .fe-cell-status')?.textContent || "";
  // There are two toasts (the "Downloading…" announce + the result); the
  // outcome is the most recent one.
  const latestToastText = () => {
    const all = document.querySelectorAll("#toast-container .toast");
    return all.length ? all[all.length - 1].textContent || "" : "";
  };

  test("shows in-row 'Downloading…' while the request is in flight", async () => {
    seedRow();
    /** @type {(v?: unknown) => void} */
    let release = () => {};
    apiFetchMock.mockReset();
    apiFetchMock.mockImplementation(
      () =>
        new Promise((res) => {
          release = res;
        })
    );

    const p = redownloadFaceEmbedderEntry("x", "X model");
    // Before the network resolves, the row must already say so.
    expect(statusText()).toMatch(/Downloading/);
    release({}); // let the POST resolve
    await p;
  });

  test("failure surfaces the real reason from the backend", async () => {
    seedRow();
    apiFetchMock.mockReset();
    // First call (the redownload POST) rejects with the backend's reason;
    // later calls (the picker reload) resolve.
    apiFetchMock.mockResolvedValue({});
    apiFetchMock.mockRejectedValueOnce(new Error("Couldn't download X model: disk full"));

    await redownloadFaceEmbedderEntry("x", "X model");

    expect(latestToastText()).toMatch(/disk full/);
  });

  test("success posts the file name and toasts completion", async () => {
    seedRow();
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({});

    await redownloadFaceEmbedderEntry("x", "X model");

    const post = apiFetchMock.mock.calls.find((c) => c[0] === "/api/v1/models/redownload");
    expect(post).toBeDefined();
    expect(JSON.parse(post[1].body)).toEqual({ name: "x.onnx" });
    expect(latestToastText()).toMatch(/downloaded/i);
  });
});

// ── "I accept" stays disabled until every box is checked ──

describe("acceptance gate — _acceptanceGateSatisfied", () => {
  const mount = (boxesChecked, { commercial = false, rightsChecked = false } = {}) => {
    const boxes = boxesChecked
      .map(
        (checked, i) =>
          `<input type="checkbox" data-checkbox-id="cb${i}"${checked ? " checked" : ""}>`
      )
      .join("");
    const rights = commercial
      ? `<input type="checkbox" id="fe-separate-rights"${rightsChecked ? " checked" : ""}>`
      : "";
    document.body.innerHTML = `
      <div id="fe-acceptance-overlay">${boxes}${rights}</div>`;
    return document.getElementById("fe-acceptance-overlay");
  };

  test("false when any required box is unchecked", () => {
    _resetFaceEmbedderStateForTests(); // use_context = unspecified
    const root = mount([true, false, true]);
    expect(_acceptanceGateSatisfied(root)).toBe(false);
  });

  test("true when every required box is checked (non-commercial)", () => {
    _resetFaceEmbedderStateForTests();
    const root = mount([true, true, true]);
    expect(_acceptanceGateSatisfied(root)).toBe(true);
  });

  test("commercial: all boxes checked but rights box unchecked → false", () => {
    _seedFaceEmbedderStateForTests({ useContext: "commercial" });
    const root = mount([true, true], { commercial: true, rightsChecked: false });
    expect(_acceptanceGateSatisfied(root)).toBe(false);
  });

  test("commercial: all boxes + rights box checked → true", () => {
    _seedFaceEmbedderStateForTests({ useContext: "commercial" });
    const root = mount([true, true], { commercial: true, rightsChecked: true });
    expect(_acceptanceGateSatisfied(root)).toBe(true);
  });
});

describe("acceptance gate wiring — button disabled state", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="toast-container"></div>
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal">
          <div id="fe-acceptance-body"></div>
          <div class="modal-actions">
            <button data-action="closeFaceEmbedderAcceptance">Cancel</button>
            <button id="fe-accept-btn" data-action="confirmFaceEmbedderAcceptance" disabled>I accept</button>
          </div>
        </div>
      </div>`;
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({
      model_id: "m",
      model_display_name: "M",
      compressed_disclaimer: "",
      full_disclaimer: "",
      commercial_use_definition: "",
      biometric_responsibility_text: "",
      produces_biometric_data: false,
      required_checkboxes: [
        { id: "a", text: "Ack A" },
        { id: "b", text: "Ack B" },
      ],
      separate_rights_assertion: "",
      ack_text_version: "v1",
      ack_text_sha256: "x",
      use_context_text_version: "v1",
      use_context_text_sha256: "y",
      use_context: "personal",
      terms_url: "https://example.invalid",
      terms_permalink_url: "https://example.invalid/x",
      terms_retrieved_at: "2026-01-01",
    });
  });

  const acceptBtn = () =>
    /** @type {HTMLButtonElement} */ (document.getElementById("fe-accept-btn"));

  test("accept button starts disabled when boxes are unchecked", async () => {
    await openFaceEmbedderAcceptance("m");
    expect(acceptBtn().disabled).toBe(true);
  });

  test("checking ALL boxes enables it; unchecking one disables it again", async () => {
    await openFaceEmbedderAcceptance("m");
    const boxes = /** @type {HTMLInputElement[]} */ ([
      ...document.querySelectorAll("input[type=checkbox][data-checkbox-id]"),
    ]);
    expect(boxes).toHaveLength(2);

    boxes[0].checked = true;
    boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "still disabled with one box unchecked").toBe(true);

    boxes[1].checked = true;
    boxes[1].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "enabled once all boxes checked").toBe(false);

    boxes[0].checked = false;
    boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "re-disabled when a box is unchecked").toBe(true);
  });

  test("REGRESSION: already-accepted model opens read-only (no empty-checkbox contradiction)", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");

    const boxes = /** @type {HTMLInputElement[]} */ ([
      ...document.querySelectorAll("input[type=checkbox][data-checkbox-id]"),
    ]);
    // The menu shows ✓; the modal must agree — boxes pre-checked, disabled.
    expect(
      boxes.every((b) => b.checked && b.disabled),
      "boxes reflect prior acceptance"
    ).toBe(true);
    expect(document.querySelector(".fe-accepted-banner")).not.toBeNull();
  });

  test("already-accepted model offers Withdraw (repurposes the primary button)", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");

    const btn = acceptBtn();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent || "").toMatch(/withdraw/i);
    expect(btn.dataset.action).toBe("revokeFaceEmbedderAcceptance");
    expect(btn.dataset.arg0).toBe("m");
    expect(btn.classList.contains("modal-btn-danger")).toBe(true);
  });

  test("not-accepted model resets the button back to 'I accept' (no leaked Withdraw)", async () => {
    // Open an accepted model first (sets Withdraw), then a fresh one.
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");
    _seedFaceEmbedderStateForTests({ acceptedIds: [] });
    await openFaceEmbedderAcceptance("m");

    const btn = acceptBtn();
    expect(btn.textContent || "").toMatch(/i accept/i);
    expect(btn.dataset.action).toBe("confirmFaceEmbedderAcceptance");
    expect(btn.dataset.arg0).toBeUndefined();
    expect(btn.classList.contains("modal-btn-danger")).toBe(false);
  });
});

describe("revokeFaceEmbedderAcceptance — withdrawal flow", () => {
  beforeEach(() => {
    document.body.innerHTML += `
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal"><div id="fe-acceptance-body"></div></div>
      </div>`;
  });

  test("confirmed withdrawal POSTs revoke and drops the model from accepted", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["insightface_buffalo_s"] });
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({});

    await revokeFaceEmbedderAcceptance("insightface_buffalo_s");

    const post = apiFetchMock.mock.calls.find(
      (c) => c[0] === "/api/v1/model-registry/acceptance/revoke"
    );
    expect(post).toBeDefined();
    expect(JSON.parse(post[1].body)).toEqual({
      model_id: "insightface_buffalo_s",
    });
  });
});

// ── Acceptance dialog dismiss behaviour (project modal convention) ──

describe("acceptance dialog ESC + click-outside dismiss", () => {
  beforeEach(() => {
    // Seed the dialog overlay + minimal draft response.
    document.body.innerHTML += `
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal">
          <div id="fe-acceptance-body"></div>
          <div class="modal-actions">
            <button data-action="closeFaceEmbedderAcceptance">Cancel</button>
            <button data-action="confirmFaceEmbedderAcceptance">I accept</button>
          </div>
        </div>
      </div>
    `;
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({
      model_id: "test_model",
      model_display_name: "Test model",
      compressed_disclaimer: "short",
      full_disclaimer: "long",
      commercial_use_definition: "",
      biometric_responsibility_text: "",
      produces_biometric_data: false,
      required_checkboxes: [],
      separate_rights_assertion: "",
      ack_text_version: "v1",
      ack_text_sha256: "abc",
      use_context_text_version: "v1",
      use_context_text_sha256: "def",
      use_context: "personal",
      terms_url: "https://example.invalid",
      terms_permalink_url: "https://example.invalid/abc",
      terms_retrieved_at: "2026-01-01",
    });
  });

  const overlayVisible = () => {
    const overlay = document.getElementById("fe-acceptance-overlay");
    return overlay?.classList.contains("visible") || false;
  };

  test("ESC dismisses an open acceptance dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
    });
    document.dispatchEvent(event);

    expect(overlayVisible()).toBe(false);
  });

  test("click on overlay backdrop dismisses the dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const overlay = document.getElementById("fe-acceptance-overlay");
    const event = new MouseEvent("click", { bubbles: true });
    // dispatch directly on the overlay (the backdrop), NOT on the
    // inner modal — clicks inside the modal must NOT dismiss.
    overlay?.dispatchEvent(event);

    expect(overlayVisible()).toBe(false);
  });

  test("click on inner modal does NOT dismiss the dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const inner = document.querySelector(".fe-acceptance-modal");
    const event = new MouseEvent("click", { bubbles: true });
    inner?.dispatchEvent(event);

    expect(overlayVisible()).toBe(true);
  });

  test("ESC is a no-op when the dialog is closed", () => {
    // Dialog starts hidden — make sure ESC doesn't throw or do
    // anything surprising when nothing is open.
    expect(overlayVisible()).toBe(false);
    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
    });
    document.dispatchEvent(event);
    expect(overlayVisible()).toBe(false);
  });
});

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
