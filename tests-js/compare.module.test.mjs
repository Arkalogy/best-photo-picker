// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _compareZoom,
  _getCompareSiblings,
  _isCompareSiblingMode,
  _resetCompareState,
  _siblingDelete,
  _siblingJump,
  _siblingNav,
  _siblingUseThis,
  closeCompare,
  compareNav,
  comparePick,
  compareSwap,
  isCompareOpen,
  openCompare,
  openCompareFromSelection,
  openCompareWithSibling,
} from "../bpp/web/static/js/modules/compare.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay">
      <div class="confirm-dialog"></div>
    </div>
    <div id="compare-overlay" onclick="closeCompare(event)">
      <div id="compare-toolbar"></div>
      <div id="compare-sibling-strip" class="hidden"></div>
      <span id="compare-left-label" class="compare-side-label"></span>
      <span id="compare-right-label" class="compare-side-label"></span>
      <img id="compare-left-img" />
      <div id="compare-left-info"></div>
      <img id="compare-right-img" />
      <div id="compare-right-info"></div>
    </div>
  `;
  // jsdom: img.getBoundingClientRect needs zero-default; HTMLElement default is fine.
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).lightboxIdx = -1;
  /** @type {any} */ (window).SCORE_LABELS = {
    blur_score: "Sharpness",
    exposure_score: "Exposure",
    face_score: "Faces",
    composition_score: "Composition",
  };
  /** @type {any} */ (window).closeLightbox = vi.fn();
  /** @type {any} */ (window).openLightbox = vi.fn();
  _resetCompareState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "currentGridItems",
    "multiSelected",
    "currentAlbumId",
    "lightboxIdx",
    "SCORE_LABELS",
    "closeLightbox",
    "openLightbox",
    "_getDupeGroups",
    "_getDupeIndex",
    "_dupeSkip",
    "_dupeSkipOrClose",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetCompareState();
});

/**
 * @param {object} body
 */
function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const samplePhotos = [
  {
    filepath: "/a.jpg",
    filename: "a.jpg",
    thumb_hash: "ha",
    aggregate_score: 0.8,
    blur_score: 0.7,
    exposure_score: 0.8,
    face_score: 0.5,
    composition_score: 0.6,
    date_day: "2024-01-01",
  },
  {
    filepath: "/b.jpg",
    filename: "b.jpg",
    thumb_hash: "hb",
    aggregate_score: 0.6,
    blur_score: 0.5,
    exposure_score: 0.6,
    face_score: 0.6,
    composition_score: 0.4,
    date_day: "2024-01-02",
  },
  {
    filepath: "/c.jpg",
    filename: "c.jpg",
    thumb_hash: "hc",
    aggregate_score: 0.7,
    date_day: "2024-01-03",
  },
];

describe("openCompare / closeCompare / isCompareOpen", () => {
  test("noop when indexes out of range", () => {
    /** @type {any} */ (window).currentGridItems = [];
    openCompare(0, 1);
    expect(isCompareOpen()).toBe(false);
  });

  test("opens overlay with both sides rendered", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    expect(isCompareOpen()).toBe(true);
    expect(document.getElementById("compare-overlay")?.classList.contains("visible")).toBe(true);
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-left-img")).src
    ).toContain("ha");
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("hb");
  });

  test("closeCompare clears overlay and resets state", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    closeCompare();
    expect(isCompareOpen()).toBe(false);
    expect(document.getElementById("compare-overlay")?.classList.contains("visible")).toBe(false);
  });

  test("closeCompare ignores non-target click events", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    const evt = /** @type {any} */ ({ target: {}, currentTarget: {} });
    closeCompare(evt);
    // target !== currentTarget => no close
    expect(isCompareOpen()).toBe(true);
  });
});

describe("openCompareFromSelection", () => {
  test("toasts when fewer than 2 selected", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a.jpg"]);
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompareFromSelection();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Select at least 2 photos"
    );
  });

  test("opens compare on first two selected paths", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a.jpg", "/b.jpg"]);
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompareFromSelection();
    expect(isCompareOpen()).toBe(true);
  });

  test("toasts when selected paths aren't in the grid", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/x.jpg", "/y.jpg"]);
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompareFromSelection();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "not found in grid"
    );
  });
});

describe("compareNav / compareSwap / comparePick", () => {
  test("compareNav wraps around bounds", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    compareNav("left", -1);
    // Left was 0, going -1 wraps to last (2)
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-left-img")).src
    ).toContain("hc");
  });

  test("compareSwap swaps the two sides", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    compareSwap();
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-left-img")).src
    ).toContain("hb");
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("ha");
  });

  test("comparePick toasts winner filename and advances loser side", async () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await comparePick("left");
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Picked a.jpg"
    );
  });

  test("comparePick POSTs include + exclude when in album", async () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    /** @type {any} */ (window).currentAlbumId = 7;
    openCompare(0, 1);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await comparePick("left");
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls).toHaveLength(2);
    expect(JSON.parse(calls[0][1].body).mode).toBe("include");
    expect(JSON.parse(calls[1][1].body).mode).toBe("exclude");
  });

  test("noop when not open", () => {
    expect(() => compareNav("left", 1)).not.toThrow();
    expect(() => compareSwap()).not.toThrow();
    expect(() => comparePick("left")).not.toThrow();
  });
});

describe("openCompareWithSibling / sibling actions", () => {
  test("opens with parent on left, sibling[0] on right", () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95, filename: "s1.jpg" },
      { filepath: "/s2.jpg", thumb_hash: "hs2", similarity: 0.85, filename: "s2.jpg" },
    ];
    openCompareWithSibling(parent, siblings, 0);
    expect(_isCompareSiblingMode()).toBe(true);
    expect(_getCompareSiblings()).toHaveLength(2);
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-left-img")).src
    ).toContain("ha");
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("hs1");
  });

  test("noop when no parent or siblings", () => {
    openCompareWithSibling(null, [], 0);
    expect(_isCompareSiblingMode()).toBe(false);
  });

  test("_siblingNav cycles forward and wraps", () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95 },
      { filepath: "/s2.jpg", thumb_hash: "hs2", similarity: 0.85 },
    ];
    openCompareWithSibling(parent, siblings, 0);
    _siblingNav(1);
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("hs2");
    _siblingNav(1);
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("hs1");
  });

  test("_siblingJump jumps to specific index", () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95 },
      { filepath: "/s2.jpg", thumb_hash: "hs2", similarity: 0.85 },
    ];
    openCompareWithSibling(parent, siblings, 0);
    _siblingJump(1);
    expect(
      /** @type {HTMLImageElement} */ (document.getElementById("compare-right-img")).src
    ).toContain("hs2");
  });

  test("_siblingUseThis POSTs include+exclude and toasts", async () => {
    /** @type {any} */ (window).currentAlbumId = 7;
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95, filename: "s1.jpg" },
    ];
    openCompareWithSibling(parent, siblings, 0);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _siblingUseThis();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls).toHaveLength(2);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Swapped: using s1.jpg"
    );
    expect(isCompareOpen()).toBe(false);
  });

  test("_siblingDelete asks for confirm + posts on accept", async () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95, filename: "s1.jpg" },
      { filepath: "/s2.jpg", thumb_hash: "hs2", similarity: 0.85, filename: "s2.jpg" },
    ];
    openCompareWithSibling(parent, siblings, 0);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = _siblingDelete();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    expect(_getCompareSiblings()).toHaveLength(1);
    // Full-filename copy + an Undo button (the recoverable-prune change).
    const t = document.querySelector("#toast-container .toast");
    expect(t?.textContent).toContain('Moved "s1.jpg" to trash');
    expect(t?.querySelector(".toast-action")?.textContent).toBe("Undo");
  });

  test("_siblingDelete declined = no POST, no state change", async () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95, filename: "s1.jpg" },
    ];
    openCompareWithSibling(parent, siblings, 0);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = _siblingDelete();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
    expect(_getCompareSiblings()).toHaveLength(1);
  });

  test("_siblingDelete closes compare when last sibling removed", async () => {
    const parent = samplePhotos[0];
    const siblings = [
      { filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95, filename: "s1.jpg" },
    ];
    openCompareWithSibling(parent, siblings, 0);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = _siblingDelete();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    expect(isCompareOpen()).toBe(false);
  });
});

describe("dupe mode label hookup", () => {
  test("'Best Photo' label appears when _getDupeGroups returns non-null", () => {
    /** @type {any} */ (window)._getDupeGroups = () => [{}];
    /** @type {any} */ (window)._getDupeIndex = () => 0;
    const parent = samplePhotos[0];
    const siblings = [{ filepath: "/s1.jpg", thumb_hash: "hs1", similarity: 0.95 }];
    openCompareWithSibling(parent, siblings, 0);
    expect(document.getElementById("compare-left-label")?.textContent).toBe("Best Photo");
    expect(document.getElementById("compare-right-label")?.innerHTML).toContain("Duplicate");
  });
});

describe("_compareZoom", () => {
  test("toggles 'zoomed' class", () => {
    const img = document.createElement("img");
    document.body.appendChild(img);
    img.getBoundingClientRect = () =>
      /** @type {DOMRect} */ ({ left: 0, top: 0, width: 100, height: 100 });
    const evt = /** @type {any} */ ({ currentTarget: img, clientX: 50, clientY: 50 });
    _compareZoom(evt);
    expect(img.classList.contains("zoomed")).toBe(true);
    _compareZoom(evt);
    expect(img.classList.contains("zoomed")).toBe(false);
  });
});

describe("_resetCompareState", () => {
  test("clears all internal state", () => {
    /** @type {any} */ (window).currentGridItems = samplePhotos;
    openCompare(0, 1);
    _resetCompareState();
    expect(isCompareOpen()).toBe(false);
    expect(_isCompareSiblingMode()).toBe(false);
    expect(_getCompareSiblings()).toEqual([]);
  });
});
