// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _openExportFolder,
  clearAnalysisCache,
  clearLibrary,
  doExport,
  hide,
  openBrowser,
  openExportBrowser,
  recomputeHashes,
  show,
  toggleExportQuality,
  validateClearConfirm,
  validateInput,
} from "../bpp/web/static/js/modules/utils.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay">
      <div class="confirm-dialog"></div>
    </div>
    <div id="my-section" class="hidden"></div>
    <div id="export-modal-overlay">
      <select id="export-format">
        <option value="jpeg" selected>JPEG</option>
        <option value="png">PNG</option>
      </select>
      <div id="export-quality-field" style="display:flex"></div>
      <input id="export-dir" />
      <input id="export-max-size" value="" />
      <input id="export-quality" value="85" />
      <button id="btn-do-export">Export</button>
      <div id="export-status"></div>
      <button data-action="hideExportModal">Cancel</button>
    </div>
    <input id="clear-confirm-input" />
    <button id="btn-clear-library">Delete All</button>
    <input id="input-dir" />
  `;
  /** @type {any} */ (window).getExportPaths = () => ["/a", "/b"];
  /** @type {any} */ (window).validateInput = vi.fn();
  /** @type {any} */ (window).hideSettings = vi.fn();
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window).loadAlbumList = vi.fn();
  /** @type {any} */ (window).showEmptyLibrary = vi.fn();
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).favorites = new Set();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "getExportPaths",
    "validateInput",
    "hideSettings",
    "renderGrid",
    "loadAlbumList",
    "showEmptyLibrary",
    "photos",
    "selectedPaths",
    "overrides",
    "favorites",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
});

/**
 * @param {object} body
 * @param {number} [status]
 */
function jsonResp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("show / hide", () => {
  test("show removes hidden, hide adds hidden", () => {
    expect(document.getElementById("my-section")?.classList.contains("hidden")).toBe(true);
    show("my-section");
    expect(document.getElementById("my-section")?.classList.contains("hidden")).toBe(false);
    hide("my-section");
    expect(document.getElementById("my-section")?.classList.contains("hidden")).toBe(true);
  });

  test("noop when id missing", () => {
    expect(() => show("nope")).not.toThrow();
    expect(() => hide("nope")).not.toThrow();
  });
});

describe("toggleExportQuality", () => {
  test("flex display when JPEG, none otherwise", () => {
    /** @type {HTMLSelectElement} */ (document.getElementById("export-format")).value = "jpeg";
    toggleExportQuality();
    expect(
      /** @type {HTMLElement} */ (document.getElementById("export-quality-field")).style.display
    ).toBe("flex");
    /** @type {HTMLSelectElement} */ (document.getElementById("export-format")).value = "png";
    toggleExportQuality();
    expect(
      /** @type {HTMLElement} */ (document.getElementById("export-quality-field")).style.display
    ).toBe("none");
  });
});

describe("doExport", () => {
  test("toasts when export dir is empty", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "";
    await doExport();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Enter an output folder path"
    );
  });

  test("renders count + Open Folder on success", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/out";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ count: 5, failed: 0, outdir: "/out" }))
    );
    await doExport();
    const html = document.getElementById("export-status")?.innerHTML || "";
    expect(html).toContain("Exported 5");
    expect(html).toContain("Open Folder");
    expect(document.getElementById("export-status")?.classList.contains("success")).toBe(true);
  });

  test("flags failed count in status + warning toast + View log action", async () => {
    /** @type {any} */ (window).showActivityLog = vi.fn();
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/out";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ count: 4, failed: 1, outdir: "/out" }))
    );
    await doExport();
    expect(document.getElementById("export-status")?.innerHTML).toContain("(1 failed)");
    const toast = /** @type {HTMLElement} */ (
      document.querySelector("#toast-container .toast.error")
    );
    expect(toast.textContent).toContain("1 photo failed");
    // Per Bug #8 / activity-log + window-onerror pattern: the toast
    // must carry a 'View log' action so the user can find which
    // photo(s) failed without spelunking through Settings tabs.
    const actionBtn = /** @type {HTMLButtonElement | null} */ (toast.querySelector("button"));
    expect(actionBtn?.textContent).toBe("View log");
    actionBtn?.click();
    expect(/** @type {any} */ (window).showActivityLog).toHaveBeenCalled();
  });

  test("on success: Export button hides + Cancel becomes Done", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/out";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ count: 5, failed: 0, outdir: "/out" }))
    );
    await doExport();
    const btn = /** @type {HTMLElement} */ (document.getElementById("btn-do-export"));
    expect(btn.style.display).toBe("none");
    const cancel = document.querySelector('#export-modal-overlay [data-action="hideExportModal"]');
    expect(cancel?.textContent).toBe("Done");
  });

  test("changing any field after success reverts to ready state", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/out";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ count: 5, failed: 0, outdir: "/out" }))
    );
    await doExport();
    // Confirm we're in the post-success state first.
    const btn = /** @type {HTMLElement} */ (document.getElementById("btn-do-export"));
    expect(btn.style.display).toBe("none");

    // User edits the output folder — fires 'input' on #export-dir.
    const dir = /** @type {HTMLInputElement} */ (document.getElementById("export-dir"));
    dir.value = "/out2";
    dir.dispatchEvent(new Event("input", { bubbles: true }));

    // Export button is back, Cancel label restored, status line cleared.
    expect(btn.style.display).toBe("block");
    expect(btn.hasAttribute("disabled") && btn.getAttribute("disabled") !== null).toBe(false);
    const cancel = document.querySelector('#export-modal-overlay [data-action="hideExportModal"]');
    expect(cancel?.textContent).toBe("Cancel");
    expect(document.getElementById("export-status")?.textContent).toBe("");
  });

  test("changing the format dropdown after success also reverts", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value = "/out";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ count: 5, failed: 0, outdir: "/out" }))
    );
    await doExport();

    const fmt = /** @type {HTMLSelectElement} */ (document.getElementById("export-format"));
    fmt.value = "png";
    fmt.dispatchEvent(new Event("change", { bubbles: true }));

    const btn = /** @type {HTMLElement} */ (document.getElementById("btn-do-export"));
    expect(btn.style.display).toBe("block");
    const cancel = document.querySelector('#export-modal-overlay [data-action="hideExportModal"]');
    expect(cancel?.textContent).toBe("Cancel");
  });
});

describe("_openExportFolder", () => {
  test("POSTs to /api/open-folder with the path", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _openExportFolder("/some/dir");
    expect(fetchMock).toHaveBeenCalled();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const call = calls[0];
    expect(String(call[0])).toContain("/api/v1/open-folder");
    expect(JSON.parse(call[1].body).path).toBe("/some/dir");
  });
});

describe("validateClearConfirm", () => {
  test("adds 'enabled' class when input matches 'delete'", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("clear-confirm-input")).value =
      "delete";
    validateClearConfirm();
    expect(document.getElementById("btn-clear-library")?.classList.contains("enabled")).toBe(true);
  });

  test("removes 'enabled' class when input doesn't match", () => {
    document.getElementById("btn-clear-library")?.classList.add("enabled");
    /** @type {HTMLInputElement} */ (document.getElementById("clear-confirm-input")).value = "x";
    validateClearConfirm();
    expect(document.getElementById("btn-clear-library")?.classList.contains("enabled")).toBe(false);
  });
});

describe("clearLibrary", () => {
  test("noop when input is not 'delete'", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("clear-confirm-input")).value = "x";
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await clearLibrary();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("DELETEs and clears state on success", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("clear-confirm-input")).value =
      "delete";
    /** @type {any} */ (window).photos = [{ filepath: "/x" }];
    /** @type {any} */ (window).selectedPaths = new Set(["/x"]);
    /** @type {any} */ (window).overrides = { "/x": "include" };
    /** @type {any} */ (window).favorites = new Set(["/x"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos_deleted: 12 }))
    );
    await clearLibrary();
    expect(/** @type {any} */ (window).photos).toEqual([]);
    expect(/** @type {any} */ (window).selectedPaths.size).toBe(0);
    expect(/** @type {any} */ (window).overrides).toEqual({});
    expect(/** @type {any} */ (window).favorites.size).toBe(0);
    expect(/** @type {any} */ (window).hideSettings).toHaveBeenCalled();
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
    expect(/** @type {any} */ (window).loadAlbumList).toHaveBeenCalled();
    expect(/** @type {any} */ (window).showEmptyLibrary).toHaveBeenCalled();
  });

  test("toasts on API error response", async () => {
    /** @type {HTMLInputElement} */ (document.getElementById("clear-confirm-input")).value =
      "delete";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "boom" }, 500))
    );
    await clearLibrary();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain("boom");
  });
});

describe("recomputeHashes", () => {
  test("'already up to date' toast when no missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ status: "done" }))
    );
    await recomputeHashes();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "All photos already have hashes"
    );
  });

  test("missing-count toast when some need hashes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ status: "started", missing: 17 }))
    );
    await recomputeHashes();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Computing hashes for 17"
    );
  });
});

describe("clearAnalysisCache", () => {
  test("noop when user declines confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = clearAnalysisCache();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("toasts 'No cache' when status is no_cache", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ status: "no_cache" }))
    );
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = clearAnalysisCache();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "No cache to clear"
    );
  });

  test("toasts cleared message when API returns ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ status: "cleared" }))
    );
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = clearAnalysisCache();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Analysis cache cleared"
    );
  });
});

describe("openBrowser / openExportBrowser", () => {
  test("openBrowser populates input-dir and calls validateInput", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ path: "/picked" }))
    );
    await openBrowser();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value).toBe(
      "/picked"
    );
    expect(/** @type {any} */ (window).validateInput).toHaveBeenCalled();
  });

  test("openExportBrowser populates export-dir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ path: "/exp" }))
    );
    await openExportBrowser();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("export-dir")).value).toBe(
      "/exp"
    );
  });

  test("openExportBrowser fires 'input' so disabled-button listeners run", async () => {
    // Real-world regression: clicking Browse populates the input
    // programmatically. Without dispatching 'input', any listener that
    // syncs UI state off keystroke events (e.g. the modal's Export
    // button enabler) never sees the new value and the button stays
    // disabled even though the field is filled.
    let inputEventFired = false;
    const inp = /** @type {HTMLInputElement} */ (document.getElementById("export-dir"));
    inp.addEventListener("input", () => {
      inputEventFired = true;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ path: "/picked-via-browse" }))
    );
    await openExportBrowser();
    expect(inp.value).toBe("/picked-via-browse");
    expect(inputEventFired).toBe(true);
  });

  test("noop when no path returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await openBrowser();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("input-dir")).value).toBe("");
  });
});

describe("validateInput", () => {
  test("is a no-op", () => {
    expect(() => validateInput()).not.toThrow();
  });
});
