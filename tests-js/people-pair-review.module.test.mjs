// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ s) => s,
}));
vi.mock("../bpp/web/static/js/modules/faces.mjs", () => ({
  loadFaceClusters: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({
  personDisplayName: vi.fn((/** @type {number} */ id) => `Person ${id}`),
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: vi.fn(),
  toastError: vi.fn(),
}));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { toast } from "../bpp/web/static/js/modules/toast.mjs";
import {
  _pairReviewContinue,
  _pairReviewKeyHandler,
  _pairSkip,
  _pairToggleContext,
  _pairVerdict,
  startFacePairReview,
} from "../bpp/web/static/js/modules/people-pair-review.mjs";

/** One reviewable pair with distinct hashes per side. */
function makePair(n) {
  const cluster = (id, hash) => ({
    id,
    name: `Person ${id}`,
    face_count: 2,
    photo_count: 2,
    representative: { thumb_hash: hash, face_index: 0 },
  });
  return {
    cluster_a: cluster(n * 10 + 1, `hash-a${n}`),
    cluster_b: cluster(n * 10 + 2, `hash-b${n}`),
    distance: 0.5,
  };
}

function faceEls() {
  const els = document.querySelectorAll("#face-pair-review-overlay .pair-review-face");
  return { a: /** @type {HTMLElement} */ (els[0]), b: /** @type {HTMLElement} */ (els[1]) };
}

beforeEach(async () => {
  document.body.innerHTML = "";
  vi.mocked(apiFetch).mockReset();
  vi.mocked(apiFetch).mockResolvedValue({
    pairs: [makePair(1), makePair(2)],
    threshold: 0.792,
  });
  await startFacePairReview();
});

afterEach(() => {
  document.removeEventListener("keydown", _pairReviewKeyHandler);
  document.body.innerHTML = "";
});

describe("pair review zoom toggle", () => {
  test("starts at the tight face crop on both sides", () => {
    const { a, b } = faceEls();
    expect(a.querySelector("img")?.getAttribute("src")).toContain("/api/v1/faces/crop/hash-a1/0");
    expect(b.querySelector("img")?.getAttribute("src")).toContain("/api/v1/faces/crop/hash-b1/0");
    expect(a.classList.contains("pair-zoomed-out")).toBe(false);
  });

  test("_pairToggleContext zooms one side out to the full photo, other side untouched", () => {
    _pairToggleContext("a");
    const { a, b } = faceEls();
    expect(a.classList.contains("pair-zoomed-out")).toBe(true);
    expect(a.querySelector("img")?.getAttribute("src")).toContain("/thumb/hash-a1");
    expect(b.classList.contains("pair-zoomed-out")).toBe(false);
    expect(b.querySelector("img")?.getAttribute("src")).toContain("/api/v1/faces/crop/");
    // Toggle back in.
    _pairToggleContext("a");
    expect(faceEls().a.querySelector("img")?.getAttribute("src")).toContain("/api/v1/faces/crop/");
  });

  test("Z key zooms both sides out together, then both back in", () => {
    const z = new KeyboardEvent("keydown", { key: "z", bubbles: true });
    document.dispatchEvent(z);
    let els = faceEls();
    expect(els.a.classList.contains("pair-zoomed-out")).toBe(true);
    expect(els.b.classList.contains("pair-zoomed-out")).toBe(true);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "z", bubbles: true }));
    els = faceEls();
    expect(els.a.classList.contains("pair-zoomed-out")).toBe(false);
    expect(els.b.classList.contains("pair-zoomed-out")).toBe(false);
  });

  test("Z with one side already out zooms BOTH out (not a blind flip)", () => {
    _pairToggleContext("a");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "z", bubbles: true }));
    const { a, b } = faceEls();
    expect(a.classList.contains("pair-zoomed-out")).toBe(true);
    expect(b.classList.contains("pair-zoomed-out")).toBe(true);
  });

  test("advancing to the next pair resets both sides to the crop", () => {
    _pairToggleContext("a");
    _pairToggleContext("b");
    _pairSkip();
    const { a, b } = faceEls();
    expect(a.querySelector("img")?.getAttribute("src")).toContain("/api/v1/faces/crop/hash-a2/0");
    expect(a.classList.contains("pair-zoomed-out")).toBe(false);
    expect(b.classList.contains("pair-zoomed-out")).toBe(false);
  });

  test("face containers carry the toggle action + a hint", () => {
    const { a } = faceEls();
    expect(a.getAttribute("data-action")).toBe("_pairToggleContext");
    expect(a.getAttribute("data-arg0")).toBe("a");
    expect(a.getAttribute("title")).toContain("whole photo");
  });
});

describe("'Same person' verdict merges", () => {
  /** Batch where pair 3 references the cluster pair 1 will absorb (id 12). */
  function threePairBatch() {
    const p3 = makePair(3);
    p3.cluster_a = { ...makePair(1).cluster_b }; // id 12 — goes stale on merge
    return [makePair(1), makePair(2), p3];
  }

  beforeEach(async () => {
    /** @type {any} */ (window).renderAlbumNav = vi.fn();
    /** @type {any} */ (window).albumList = [];
    vi.mocked(toast).mockClear();
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValueOnce({ pairs: threePairBatch(), threshold: 0.792 });
    await startFacePairReview();
  });

  afterEach(() => {
    delete (/** @type {any} */ (window).renderAlbumNav);
    delete (/** @type {any} */ (window).albumList);
  });

  test("merge response → toast names the merge, albums applied, stale pairs dropped", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      status: "recorded",
      verdict: "same",
      merged: true,
      primary_cluster_id: 11,
      absorbed_cluster_id: 12,
      albums: [{ id: 5, name: "Leo" }],
      undo: { absorbed_cluster_id: 12, primary_cluster_id: 11, faces: [[1, null]] },
    });
    await _pairVerdict("same");

    const label = vi.mocked(toast).mock.calls.at(-1)?.[0];
    expect(label).toBe("Person 12's 2 faces moved into Person 11 — Person 12 is gone from People");
    expect(/** @type {any} */ (window).albumList).toEqual([{ id: 5, name: "Leo" }]);
    expect(/** @type {any} */ (window).renderAlbumNav).toHaveBeenCalled();
    // Pair 3 referenced the absorbed cluster → dropped; batch is now 2.
    expect(
      document.querySelector("#face-pair-review-overlay .pair-review-progress")?.textContent
    ).toBe("2 of 2");
  });

  test("toast Undo round-trips the snapshot to the undo endpoint", async () => {
    const snapshot = { absorbed_cluster_id: 12, primary_cluster_id: 11, faces: [[1, null]] };
    vi.mocked(apiFetch).mockResolvedValueOnce({
      merged: true,
      primary_cluster_id: 11,
      absorbed_cluster_id: 12,
      albums: [],
      undo: snapshot,
    });
    await _pairVerdict("same");
    vi.mocked(apiFetch).mockResolvedValueOnce({ undone: true, albums: [] });
    const undoFn = vi.mocked(toast).mock.calls.at(-1)?.[2]?.action?.fn;
    expect(undoFn).toBeTypeOf("function");
    await undoFn();

    const undoCall = vi.mocked(apiFetch).mock.calls.at(-1);
    expect(undoCall?.[0]).toContain("/verdict/undo");
    const body = JSON.parse(undoCall?.[1]?.body);
    expect(body.verdict).toBe("same");
    expect(body.undo).toEqual(snapshot);
  });

  test("'different' verdict keeps the record-only toast", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ status: "recorded", verdict: "different" });
    await _pairVerdict("different");
    expect(vi.mocked(toast).mock.calls.at(-1)?.[0]).toBe("Person 11 + Person 12: different people");
  });
});

describe("end-of-batch card", () => {
  beforeEach(async () => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValueOnce({
      pairs: [makePair(1), makePair(2)],
      threshold: 0.792,
    });
    await startFacePairReview();
  });

  test("finishing the batch shows the done card with remaining count, not a closed overlay", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ count: 5 });
    _pairSkip();
    _pairSkip(); // past the end
    const overlay = document.getElementById("face-pair-review-overlay");
    expect(overlay?.classList.contains("visible")).toBe(true);
    expect(overlay?.textContent).toContain("Batch done");
    expect(overlay?.textContent).toContain("2 pairs this session");
    await vi.waitFor(() => {
      expect(document.getElementById("pair-review-remaining")?.textContent).toContain("5");
    });
    const btn = /** @type {HTMLButtonElement} */ (
      document.getElementById("pair-review-continue-btn")
    );
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain("next 5");
  });

  test("nothing left → no Continue button, explicit all-done message", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ count: 0 });
    _pairSkip();
    _pairSkip();
    await vi.waitFor(() => {
      expect(document.getElementById("pair-review-remaining")?.textContent).toContain(
        "all of them"
      );
    });
    expect(document.getElementById("pair-review-continue-btn")).toBeNull();
  });

  test("Continue loads the next batch and keeps cumulative session stats", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ count: 2 });
    _pairSkip();
    _pairSkip();
    // Continue → next batch of 2; finish it too → done card shows 4 total.
    vi.mocked(apiFetch).mockResolvedValueOnce({
      pairs: [makePair(3), makePair(4)],
      threshold: 0.792,
    });
    await _pairReviewContinue();
    expect(
      document.querySelector("#face-pair-review-overlay .pair-review-progress")?.textContent
    ).toBe("1 of 2");
    vi.mocked(apiFetch).mockResolvedValueOnce({ count: 0 });
    _pairSkip();
    _pairSkip();
    expect(document.getElementById("face-pair-review-overlay")?.textContent).toContain(
      "4 pairs this session"
    );
  });
});
