// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _dupeAdvance,
  _dupeSkip,
  _dupeSkipOrClose,
  _endDupeReview,
  _getDupeGroups,
  _getDupeIndex,
  _resetDupeState,
  _showDupeGroup,
  startDupeReview,
} from "../bpp/web/static/js/modules/dupe-review.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `<div id="toast-container"></div>`;
  /** @type {any} */ (window).openCompareWithSibling = vi.fn();
  /** @type {any} */ (window).closeCompare = vi.fn();
  _resetDupeState();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).openCompareWithSibling);
  delete (/** @type {any} */ (window).closeCompare);
  delete (/** @type {any} */ (window)._siblingUseThis);
  delete (/** @type {any} */ (window)._siblingDelete);
  delete (/** @type {any} */ (window)._getCompareSiblings);
});

describe("startDupeReview", () => {
  test("toasts when no groups returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ groups: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await startDupeReview();
    expect(_getDupeGroups()).toBeNull();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "No duplicate groups"
    );
  });

  test("opens compare on the first group when groups exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              groups: [
                {
                  photos: [
                    { filepath: "/a.jpg", thumb_hash: "ha", aggregate_score: 0.9 },
                    { filepath: "/b.jpg", thumb_hash: "hb", aggregate_score: 0.7 },
                  ],
                },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await startDupeReview();
    expect(_getDupeGroups()?.length).toBe(1);
    expect(_getDupeIndex()).toBe(0);
    expect(/** @type {any} */ (window).openCompareWithSibling).toHaveBeenCalled();
  });

  test("toasts an error when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      })
    );
    await startDupeReview();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't load duplicate groups"
    );
  });
});

describe("_showDupeGroup", () => {
  test("calls openCompareWithSibling with parent + similar_photos shape", () => {
    /** @type {any} */
    const groups = [
      {
        photos: [
          { filepath: "/best.jpg", thumb_hash: "hb", aggregate_score: 0.95, blur_score: 0.9 },
          { filepath: "/dup1.jpg", thumb_hash: "hd1", aggregate_score: 0.7 },
          { filepath: "/dup2.jpg", thumb_hash: "hd2", aggregate_score: 0.6 },
        ],
      },
    ];
    /** @type {any} */ (window).openCompareWithSibling = vi.fn();
    // Inject groups directly via the test surface
    _resetDupeState();
    /** @type {any} */ (window).fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ groups }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    return startDupeReview().then(() => {
      const call = /** @type {any} */ (window).openCompareWithSibling.mock.calls[0];
      expect(call[0].filepath).toBe("/best.jpg");
      expect(call[1]).toHaveLength(2);
      expect(call[1][0].filepath).toBe("/dup1.jpg");
      expect(call[1][0].similarity).toBe(1.0);
      expect(call[2]).toBe(0);
    });
  });

  test("ends the review when index runs past the end", () => {
    _resetDupeState();
    _showDupeGroup(99);
    expect(_getDupeGroups()).toBeNull();
  });
});

describe("_dupeSkip", () => {
  test("advances index, closes compare, schedules next show on 200ms", async () => {
    /** @type {any} */
    (window).fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            groups: [
              { photos: [{ filepath: "/a.jpg", thumb_hash: "ha", aggregate_score: 0.9 }] },
              { photos: [{ filepath: "/b.jpg", thumb_hash: "hb", aggregate_score: 0.8 }] },
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
    );
    await startDupeReview();
    /** @type {any} */ (window).openCompareWithSibling.mockClear();
    _dupeSkip();
    expect(_getDupeIndex()).toBe(1);
    expect(/** @type {any} */ (window).closeCompare).toHaveBeenCalled();
    // Advance fake timers past the 200ms scheduled show
    vi.advanceTimersByTime(200);
    expect(/** @type {any} */ (window).openCompareWithSibling).toHaveBeenCalled();
  });
});

describe("_dupeSkipOrClose", () => {
  test("closeCompare only when no review active", () => {
    _resetDupeState();
    _dupeSkipOrClose();
    expect(/** @type {any} */ (window).closeCompare).toHaveBeenCalled();
  });

  test("ends review and closes when review active", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              groups: [
                { photos: [{ filepath: "/a.jpg", thumb_hash: "ha", aggregate_score: 0.9 }] },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await startDupeReview();
    _dupeSkipOrClose();
    expect(_getDupeGroups()).toBeNull();
    expect(/** @type {any} */ (window).closeCompare).toHaveBeenCalled();
  });
});

describe("_endDupeReview", () => {
  test("toasts 'No duplicates to review' when reviewed=0", () => {
    _resetDupeState();
    _endDupeReview();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "No duplicates to review"
    );
  });
});
