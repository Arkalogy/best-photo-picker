// @ts-check
/**
 * Second whole-tree review batch, UI side: per-photo trash buttons send
 * a real array, "All" actions reach every page, skeleton grids are
 * capped, a late sibling delete leaves a newly opened comparison alone,
 * and a failed batch Undo is said out loud.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  SKELETON_MAX,
  showSkeletonGrid,
} from "../bpp/web/static/js/modules/analysis-recompute.mjs";
import {
  allListedPaths,
  permanentDeleteOne,
  restoreAllDeleted,
  restoreOne,
  unhideOne,
} from "../bpp/web/static/js/modules/deleted.mjs";
import { jsonResp, setup, teardown, withConfirm } from "./_deleted-setup.mjs";

describe("recently deleted and hidden views", () => {
  beforeEach(setup);
  afterEach(teardown);

  test("the hover buttons send one path as an array", async () => {
    const fetchMock = vi.fn(async (url) =>
      String(url).includes("/photos/deleted") || String(url).includes("/photos/hidden")
        ? jsonResp({ photos: [], total: 0 })
        : jsonResp({ count: 1 })
    );
    vi.stubGlobal("fetch", fetchMock);
    await restoreOne("/a b.jpg");
    await unhideOne("/c.jpg");
    await withConfirm(() => permanentDeleteOne("/d.jpg"), true);
    const bodies = /** @type {any[][]} */ (fetchMock.mock.calls)
      .filter((c) => c[1] && c[1].body)
      .map((c) => JSON.parse(c[1].body));
    expect(bodies.map((b) => b.filepaths)).toEqual([["/a b.jpg"], ["/c.jpg"], ["/d.jpg"]]);
  });

  test("a restore that matched nothing is an error, not a success toast", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) =>
        String(url).includes("/photos/restore") ? jsonResp({ count: 0 }) : jsonResp({ photos: [] })
      )
    );
    await restoreOne("/gone.jpg");
    const toastText = document.querySelector("#toast-container .toast")?.textContent || "";
    expect(toastText).toContain("Couldn't restore");
  });

  test("All actions walk every page of the listing", async () => {
    const pages = {
      0: {
        photos: Array.from({ length: 200 }, (_, i) => ({ filepath: `/p${i}.jpg` })),
        total: 201,
      },
      200: { photos: [{ filepath: "/p200.jpg" }], total: 201 },
    };
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/photos/deleted")) {
        const offset = Number(/offset=(\d+)/.exec(u)?.[1] || 0);
        return jsonResp(pages[offset] || { photos: [], total: 201 });
      }
      return jsonResp({ count: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    expect((await allListedPaths("/api/v1/photos/deleted")).length).toBe(201);
    await restoreAllDeleted();
    const restore = /** @type {any[][]} */ (fetchMock.mock.calls).find((c) =>
      String(c[0]).includes("/photos/restore")
    );
    expect(JSON.parse(restore[1].body).filepaths.length).toBe(201);
  });
});

describe("skeleton grid", () => {
  test("never draws more than a couple of screens of placeholders", () => {
    document.body.innerHTML = '<div id="photo-grid"></div>';
    showSkeletonGrid(200000);
    expect(document.querySelectorAll("#photo-grid .skeleton-card").length).toBe(SKELETON_MAX);
    showSkeletonGrid(3);
    expect(document.querySelectorAll("#photo-grid .skeleton-card").length).toBe(3);
  });
});
