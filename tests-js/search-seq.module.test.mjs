// @ts-check
// Verify the search-debounce race fix: when two doSearch() calls overlap,
// only the most recent one's results are allowed to paint the dropdown.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { doSearch, _resetSearchSeqForTests } from "../bpp/web/static/js/modules/search.mjs";

/**
 * Build a fetch stub that resolves to `body` after `delayMs`, honouring
 * the incoming AbortSignal so the test can observe cancellation.
 *
 * @param {any} body
 * @param {number} [delayMs]
 */
function makeDelayedFetch(body, delayMs = 0) {
  return vi.fn(
    (/** @type {string} */ _url, /** @type {RequestInit} */ opts) =>
      new Promise((resolve, reject) => {
        const signal = opts && opts.signal;
        const t = setTimeout(() => {
          resolve(
            new Response(JSON.stringify(body), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            })
          );
        }, delayMs);
        if (signal) {
          signal.addEventListener("abort", () => {
            clearTimeout(t);
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }
      })
  );
}

describe("doSearch sequence guard", () => {
  beforeEach(() => {
    _resetSearchSeqForTests();
    document.body.innerHTML = '<div id="search-results"></div>';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("a single query renders its results into #search-results", async () => {
    vi.stubGlobal(
      "fetch",
      makeDelayedFetch({ albums: [], people: [], pets: [], photos: [], tags: [] }, 0)
    );
    await doSearch("leo");
    // The dropdown should NOT show the error message.
    const html = /** @type {HTMLElement} */ (document.getElementById("search-results")).innerHTML;
    expect(html).not.toContain("Error searching");
  });

  test("a second query aborts the first and only the second renders", async () => {
    /** @type {(() => void) | undefined} */
    let firstResolve;
    /** @type {AbortSignal | undefined} */
    let firstSignal;
    const firstFetch = vi.fn(
      (/** @type {string} */ _url, /** @type {RequestInit} */ opts) =>
        new Promise((resolve, reject) => {
          firstSignal = opts && opts.signal;
          firstResolve = () =>
            resolve(
              new Response(JSON.stringify({ marker: "stale-leo" }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
              })
            );
          if (firstSignal) {
            firstSignal.addEventListener("abort", () => {
              const err = new Error("aborted");
              err.name = "AbortError";
              reject(err);
            });
          }
        })
    );
    vi.stubGlobal("fetch", firstFetch);
    const p1 = doSearch("leo");
    // Now swap in a fast second fetch that will resolve immediately with
    // the "fresh" result.
    vi.stubGlobal(
      "fetch",
      makeDelayedFetch(
        { albums: [], people: [], pets: [], photos: [], tags: [], marker: "fresh-leon" },
        0
      )
    );
    const p2 = doSearch("leon");
    await p2;
    // p2 finished first → it should have aborted p1's controller.
    expect(firstSignal?.aborted).toBe(true);
    // Now let p1's underlying fetch attempt to resolve. The abort already
    // fired so the promise rejects — and even if it didn't, the seq check
    // would drop the render. Either way: no "stale-leo" in the DOM.
    if (firstResolve) firstResolve();
    await p1.catch(() => {});
    const html = /** @type {HTMLElement} */ (document.getElementById("search-results")).innerHTML;
    expect(html).not.toContain("stale-leo");
  });

  test("a server-side error renders the error row only when query is current", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: "boom" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          })
        )
      )
    );
    await doSearch("leo");
    const html = /** @type {HTMLElement} */ (document.getElementById("search-results")).innerHTML;
    expect(html).toContain("Error searching");
  });
});
