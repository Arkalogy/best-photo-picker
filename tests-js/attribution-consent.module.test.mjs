// @ts-check
// The pre-analysis dialog's combined acceptance of attribution-licensed
// models (SFace, YuNet, MediaPipe, Haar): one checkbox instead of a
// separate Settings → Models dialog per model.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));
const toastErrorMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: vi.fn(),
  toastError: toastErrorMock,
}));

const { attributionOkLabel, attributionSectionHTML } =
  await import("../bpp/web/static/js/modules/attribution-consent.mjs");
const { analyzePreflightConsent } =
  await import("../bpp/web/static/js/modules/analysis-preflight.mjs");

const BUNDLE = {
  models: [
    {
      id: "opencv_yunet",
      display_name: "OpenCV YuNet (face detection)",
      kind: "face_detector",
      license: "Apache 2.0",
      terms_url: "https://example.com/yunet",
    },
    {
      id: "opencv_haar_frontalface",
      display_name: "OpenCV Haar <cascade>",
      kind: "face_detector",
      license: "BSD 3-Clause",
      terms_url: "https://example.com/haar",
    },
  ],
  compressed_disclaimer: "These models are open source with attribution terms.",
  full_disclaimer: "full",
  biometric_responsibility_text: "Face data is biometric.",
  required_checkboxes: [{ id: "permissive_attribution_acknowledged", text: "I acknowledge." }],
};
const EMPTY_BUNDLE = { ...BUNDLE, models: [], biometric_responsibility_text: "" };

describe("attributionSectionHTML", () => {
  const html = attributionSectionHTML(BUNDLE);

  test("lists every model with its license and a terms link", () => {
    expect(html).toContain("OpenCV YuNet (face detection)");
    expect(html).toContain("Apache 2.0");
    expect(html).toContain("BSD 3-Clause");
    expect(html).toContain('href="https://example.com/haar"');
  });

  test("escapes model names", () => {
    expect(html).toContain("OpenCV Haar &lt;cascade&gt;");
    expect(html).not.toContain("<cascade>");
  });

  test("renders the notice, the biometric note, and an unticked checkbox", () => {
    expect(html).toContain("open source with attribution terms");
    expect(html).toContain("Face data is biometric.");
    document.body.innerHTML = html;
    const box = /** @type {HTMLInputElement} */ (document.getElementById("ml-attr-ack"));
    expect(box.type).toBe("checkbox");
    expect(box.checked).toBe(false);
    expect(document.body.textContent).toContain("I acknowledge.");
  });

  test("says what is lost without them", () => {
    expect(html).toMatch(/Without them/);
  });
});

describe("attributionOkLabel", () => {
  test("names what the click does", () => {
    expect(attributionOkLabel(true, false)).toBe("Accept & analyze");
    expect(attributionOkLabel(true, true)).toBe("Accept, download & analyze");
    expect(attributionOkLabel(false, true)).toBe("Analyze without them");
  });
});

/**
 * Stub appConfirm: render the dialog body, let the test act on it,
 * then resolve with the OK/Cancel answer.
 * @param {(doc: Document) => boolean} act
 */
function stubDialog(act) {
  /** @type {any} */ (window).appConfirm = vi.fn((_title, _sub, opts) => {
    document.body.innerHTML = `${opts.bodyHTML}<button id="confirm-ok">${opts.okLabel}</button>`;
    return Promise.resolve().then(() => {
      // analyzePreflightConsent catches errors and falls back to a
      // generic prompt, which would hide a failed check made in here —
      // record it so afterEach fails the test.
      try {
        return act(document);
      } catch (e) {
        dialogErrors.push(e);
        throw e;
      }
    });
  });
}

/** @type {unknown[]} */
const dialogErrors = [];
afterEach(() => {
  const errors = dialogErrors.splice(0);
  expect(errors).toEqual([]);
});

/**
 * @param {{bundle?: any, pending?: Record<string, any>,
 *          confirm?: (body: any) => any}} [opts]
 */
function routeApi({ bundle = BUNDLE, pending = {}, confirm = (_body) => ({ accepted: [] }) } = {}) {
  apiFetchMock.mockImplementation(async (url, opts) => {
    if (url === "/api/v1/models/pending") {
      return {
        models: [],
        total_mb: 0,
        attribution: [],
        attribution_mb: 0,
        blocked: [],
        ...pending,
      };
    }
    if (url === "/api/v1/model-registry/acceptance/attribution-bundle") return bundle;
    if (url.endsWith("/attribution-bundle/confirm")) return confirm(JSON.parse(opts.body));
    throw new Error(`unexpected ${url}`);
  });
}

function tick(doc) {
  const box = /** @type {HTMLInputElement} */ (doc.getElementById("ml-attr-ack"));
  box.checked = true;
  box.dispatchEvent(new Event("change"));
}

beforeEach(() => {
  apiFetchMock.mockReset();
  toastErrorMock.mockReset();
  document.body.innerHTML = "";
});

describe("analyzePreflightConsent with attribution models pending", () => {
  test("ticking the box accepts every listed model, then analyzes", async () => {
    const confirm = vi.fn(() => ({ accepted: ["opencv_yunet", "opencv_haar_frontalface"] }));
    routeApi({ confirm });
    stubDialog((doc) => {
      tick(doc);
      expect(doc.getElementById("confirm-ok")?.textContent).toBe("Accept & analyze");
      return true;
    });
    const heard = vi.fn();
    document.addEventListener("bpp:models-changed", heard);
    await expect(analyzePreflightConsent()).resolves.toBe(true);
    document.removeEventListener("bpp:models-changed", heard);
    expect(confirm).toHaveBeenCalledWith({
      model_ids: ["opencv_yunet", "opencv_haar_frontalface"],
      checkbox_responses: { permissive_attribution_acknowledged: true },
    });
    expect(heard).toHaveBeenCalledTimes(1); // Settings / hint refresh
  });

  test("leaving it unticked analyzes without accepting anything", async () => {
    const confirm = vi.fn();
    routeApi({ confirm });
    stubDialog((doc) => {
      expect(doc.getElementById("confirm-ok")?.textContent).toBe("Analyze without them");
      return true;
    });
    await expect(analyzePreflightConsent()).resolves.toBe(true);
    expect(confirm).not.toHaveBeenCalled();
  });

  test("a failed acceptance names the action and does not analyze", async () => {
    routeApi({
      confirm: () => {
        throw new Error("disk full");
      },
    });
    stubDialog((doc) => {
      tick(doc);
      return true;
    });
    await expect(analyzePreflightConsent()).resolves.toBe(false);
    expect(toastErrorMock).toHaveBeenCalledWith("accept the model licenses", expect.any(Error));
  });

  test("cancel neither accepts nor analyzes", async () => {
    const confirm = vi.fn();
    routeApi({ confirm });
    stubDialog((doc) => {
      tick(doc);
      return false;
    });
    await expect(analyzePreflightConsent()).resolves.toBe(false);
    expect(confirm).not.toHaveBeenCalled();
  });

  test("downloads that wait on the acceptance are marked as such", async () => {
    routeApi({
      pending: {
        attribution: [{ name: "Selfie segmenter", size_mb: 0.3, host: "storage.googleapis.com" }],
        attribution_mb: 0.3,
      },
    });
    stubDialog((doc) => {
      expect(doc.body.textContent).toContain("Selfie segmenter");
      expect(doc.body.textContent).toContain("if you accept");
      expect(doc.getElementById("confirm-ok")?.textContent).toBe("Analyze without them");
      tick(doc);
      expect(doc.getElementById("confirm-ok")?.textContent).toBe("Accept, download & analyze");
      return true;
    });
    await expect(analyzePreflightConsent()).resolves.toBe(true);
  });

  test("nothing pending and nothing to accept: no dialog", async () => {
    routeApi({ bundle: EMPTY_BUNDLE });
    stubDialog(() => true);
    await expect(analyzePreflightConsent()).resolves.toBe(true);
    expect(/** @type {any} */ (window).appConfirm).not.toHaveBeenCalled();
  });
});

describe("downloadSizeLabel", () => {
  test("megabytes, kilobytes, and unknown size", async () => {
    const { downloadSizeLabel } =
      await import("../bpp/web/static/js/modules/analysis-preflight.mjs");
    expect(downloadSizeLabel(38)).toBe("38 MB");
    expect(downloadSizeLabel(0.3)).toBe("307 KB");
    expect(downloadSizeLabel(0)).toBe("size unknown");
  });
});

describe("downloadTotalLabel", () => {
  test("says when part of the total is unknown", async () => {
    const { downloadTotalLabel } =
      await import("../bpp/web/static/js/modules/analysis-preflight.mjs");
    expect(downloadTotalLabel(38.4, false)).toBe("38.4 MB");
    expect(downloadTotalLabel(336, false)).toBe("336 MB");
    expect(downloadTotalLabel(38.4, true)).toBe("38.4 MB + plugin models of unknown size");
    expect(downloadTotalLabel(0, true)).toBe("size unknown");
  });
});

describe("blocked plugin models in the pre-analysis prompt", () => {
  test("a blocked plugin model is listed even when its kind is not a built-in one", async () => {
    routeApi({
      bundle: EMPTY_BUNDLE,
      pending: {
        blocked: [
          {
            name: "Aesthetic scorer",
            kind: "aesthetic_scorer",
            reason: "needs_license",
            from_plugin: true,
          },
        ],
      },
    });
    /** @type {string[]} */
    const shown = [];
    stubDialog((doc) => {
      shown.push(doc.body.textContent || "");
      return true;
    });
    await expect(analyzePreflightConsent()).resolves.toBe(true);
    expect(shown).toHaveLength(1); // the real dialog, not the error fallback
    expect(shown[0]).toContain("Aesthetic scorer");
  });
});

describe("blockedItemLabel for plugin models", () => {
  test("names the plugin model instead of a built-in capability", async () => {
    const { blockedItemLabel } =
      await import("../bpp/web/static/js/modules/analysis-preflight.mjs");
    expect(blockedItemLabel({ name: "RetinaFace", kind: "face_detector", from_plugin: true })).toBe(
      "Plugin model: RetinaFace"
    );
    expect(
      blockedItemLabel({
        name: "RetinaFace",
        kind: "face_detector",
        from_plugin: true,
        reason: "needs_reaccept",
      })
    ).toContain("Plugin model: RetinaFace — license terms updated");
  });
});
