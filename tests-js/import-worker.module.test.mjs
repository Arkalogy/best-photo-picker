// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Capture the SSE handler from listenImportProgress without a real stream.
// apiFetch stays real (existing startImport tests stub global fetch).
const h = vi.hoisted(() => ({ es: /** @type {any} */ (null) }));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  authEventSource: () => {
    const es = { onmessage: null, onerror: null, close() {} };
    h.es = es;
    return es;
  },
}));

import {
  _phaseLabel,
  _showImportSummary,
  listenImportProgress,
  startImport,
} from "../bpp/web/static/js/modules/import-worker.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <input id="input-dir" value="">
    <div id="toast-container"></div>
  `;
  /** @type {any} */ (window).showStatusProgress = vi.fn();
  /** @type {any} */ (window).hideStatusProgress = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).showStatusProgress);
  delete (/** @type {any} */ (window).hideStatusProgress);
  delete (/** @type {any} */ (window).activeOperation);
});

const lastToast = () =>
  /** @type {HTMLElement | null} */ (document.querySelector("#toast-container .toast"));

describe("startImport", () => {
  test("rejects empty input dir without firing fetch", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    /** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value = "";
    await startImport();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(lastToast()?.textContent).toContain("Enter a photo folder path");
  });

  test("rejects whitespace-only input dir", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    /** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value = "   ";
    await startImport();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("on server error response, toasts the error and clears state", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value = "/some/dir";
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "Already importing" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await startImport();
    expect(lastToast()?.textContent).toContain("Already importing");
    expect(/** @type {any} */ (window).activeOperation).toBeNull();
    expect(/** @type {any} */ (window).hideStatusProgress).toHaveBeenCalled();
  });
});

describe("_showImportSummary", () => {
  test("'No supported photos found' when all counts are 0", () => {
    _showImportSummary({ imported: 0, skipped: 0, errors: 0 });
    expect(lastToast()?.textContent).toContain("No supported photos found");
    expect(lastToast()?.classList.contains("error")).toBe(true);
  });

  test("'Imported N photos' for happy path", () => {
    _showImportSummary({ imported: 5 });
    expect(lastToast()?.textContent).toBe("Imported 5 photos");
  });

  test("singular 'photo' for count of 1", () => {
    _showImportSummary({ imported: 1 });
    expect(lastToast()?.textContent).toBe("Imported 1 photo");
  });

  test("appends batch name when provided", () => {
    _showImportSummary({ imported: 3, batch_name: "vacation_2024" });
    expect(lastToast()?.textContent).toContain('from "vacation_2024"');
  });

  test("appends skipped + errors as parens-list", () => {
    _showImportSummary({ imported: 5, skipped: 3, errors: 2 });
    expect(lastToast()?.textContent).toContain("3 duplicates skipped");
    expect(lastToast()?.textContent).toContain("2 errors");
  });

  test("singular 'duplicate'/'error' for count of 1", () => {
    _showImportSummary({ imported: 5, skipped: 1, errors: 1 });
    expect(lastToast()?.textContent).toContain("1 duplicate skipped");
    expect(lastToast()?.textContent).toContain("1 error)");
  });

  test("imported=0 + skipped>0 → warning toast", () => {
    _showImportSummary({ imported: 0, skipped: 3 });
    expect(lastToast()?.classList.contains("warning")).toBe(true);
    expect(lastToast()?.textContent).toContain("3 duplicates");
  });
});

describe("_phaseLabel", () => {
  test.each([
    ["importing", "Importing photos…"],
    ["scanning", "Finding photos…"],
    ["models", "Loading ML models…"],
    ["scoring", "Scoring photos…"],
    ["analyzing", "Preparing analysis…"],
    ["faces", "Detecting faces…"],
    ["clip", "Computing semantic search index…"],
  ])("maps server phase '%s' to %j", (phase, label) => {
    expect(_phaseLabel(phase)).toBe(label);
  });

  test("falls back to generic 'Analyzing…' for unknown phases", () => {
    // A future server phase the client doesn't know about must not
    // crash or render the empty string — fallback is the current
    // 'Analyzing…' string, same as the pre-fix behavior.
    expect(_phaseLabel("nonexistent")).toBe("Analyzing…");
    expect(_phaseLabel("")).toBe("Analyzing…");
  });
});

describe("listenImportProgress — status/warning are not silent (release audit P-01)", () => {
  /** @param {object} msg */
  function emit(msg) {
    h.es.onmessage({ data: JSON.stringify(msg) });
  }

  beforeEach(() => {
    document.body.innerHTML =
      '<div id="toast-container"></div>' +
      '<main class="analyzing-pending"><div id="analyzing-banner"></div></main>';
    h.es = null;
  });

  test("status message surfaces in the status bar + analyzing banner", () => {
    listenImportProgress();
    emit({ type: "status", message: "Downloading SCRFD face detector (3 MB)…" });
    expect(/** @type {any} */ (window).showStatusProgress).toHaveBeenCalledWith(
      "Downloading SCRFD face detector (3 MB)…",
      0
    );
    expect(document.getElementById("analyzing-banner")?.textContent).toContain("Downloading SCRFD");
  });

  test("warning message shows a toast (not dropped)", () => {
    listenImportProgress();
    emit({ type: "warning", message: "SCRFD download failed — using fallback detectors" });
    expect(lastToast()?.textContent).toContain("fallback detectors");
  });
});
