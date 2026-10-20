// @ts-check
/**
 * Every mutating handler names the action, the reason and a way out when
 * it fails (CLAUDE.md error-toast policy), and never leaves a control
 * stuck. Three handlers slipped past that: Apply on the face-threshold
 * slider (a failed request left the button on "Applying…"), Name &
 * Confirm in the people review (an unhandled rejection) and the analyze /
 * import progress streams (a bare "Analysis failed").
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/albums.mjs", () => ({ loadAlbumList: vi.fn() }));

import { applyFaceRecluster } from "../bpp/web/static/js/modules/faces.mjs";
import { _reviewConfirm, _setReviewState } from "../bpp/web/static/js/modules/people-review.mjs";
import { listenProgress } from "../bpp/web/static/js/modules/analysis.mjs";
import { listenImportProgress } from "../bpp/web/static/js/modules/import-worker.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";
import { setup, teardown } from "./_analysis-setup.mjs";

/** @type {any[]} */
let sources = [];

beforeEach(() => {
  setup();
  document.body.insertAdjacentHTML(
    "beforeend",
    `<input id="face-cluster-slider" type="range" value="0.55" />
     <button id="btn-recluster">Apply</button>
     <input id="review-name-field" value="Kai" />`
  );
  sources = [];
  /** @type {any} */ (globalThis).EventSource = class {
    constructor() {
      this.onmessage = null;
      this.onerror = null;
      sources.push(this);
    }
    close() {}
  };
});

afterEach(() => {
  teardown();
  vi.unstubAllGlobals();
});

const toasts = () =>
  [...document.querySelectorAll("#toast-container .toast")].map((t) => t.textContent || "");

function failingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(JSON.stringify({ error: "database is locked" }), { status: 503 })
    )
  );
}

describe("Apply on the face threshold", () => {
  test("a failed request re-enables the button and names the failure", async () => {
    failingFetch();
    await applyFaceRecluster();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("btn-recluster"));
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toBe("Apply");
    expect(toasts().join(" ")).toMatch(/Couldn't regroup faces: database is locked/);
  });
});

describe("Name & Confirm in the people review", () => {
  test("a failed rename is a toast, not an unhandled rejection", async () => {
    // Only the rename itself fails; the reads around it succeed.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (/** @type {string} */ _url, /** @type {any} */ init) =>
        init?.method === "PUT"
          ? new Response(JSON.stringify({ error: "database is locked" }), { status: 503 })
          : new Response(JSON.stringify({ albums: [] }), { status: 200 })
      )
    );
    /** @type {any} */ (state).albumList = [
      { id: 9, album_type: "smart_person", rule: { cluster_id: 4 } },
    ];
    _setReviewState({ unreviewed: [{ cluster_id: 4, filepaths: ["/a.jpg"] }], reviewed: 0 }, 0);
    await _reviewConfirm();
    expect(toasts().join(" ")).toMatch(/Couldn't name the person: database is locked/);
  });
});

describe("progress streams", () => {
  test("an analyze error names the action and the reason", () => {
    listenProgress();
    sources[0].onmessage({
      data: JSON.stringify({ type: "error", message: "model file corrupt" }),
    });
    expect(toasts().join(" ")).toMatch(/Couldn't analyze the library: model file corrupt/);
  });

  test("an import error names the action and the reason", () => {
    listenImportProgress();
    sources[0].onmessage({ data: JSON.stringify({ type: "error", message: "disk full" }) });
    expect(toasts().join(" ")).toMatch(/Couldn't import the photos: disk full/);
  });
});
