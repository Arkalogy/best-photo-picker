// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ s) => s,
}));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import {
  loadTagsList,
  navigateToTagPhotos,
  showTagsView,
} from "../bpp/web/static/js/modules/tags-view.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="content"><div id="photo-grid"></div></div>
    <div id="status-summary"></div>`;
  /** @type {any} */ (window).ICONS = { tag: "<svg></svg>" };
  /** @type {any} */ (window).vgrid = { items: [{ x: 1 }] };
  vi.mocked(apiFetch).mockReset();
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("showTagsView", () => {
  test("renders one card per tag with cover, name, count", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      tags: [
        { id: 1, name: "birthday", count: 12, cover_thumb_hash: "abc" },
        { id: 2, name: "beach", count: 1, cover_thumb_hash: null },
      ],
    });
    await loadTagsList();
    showTagsView();
    const cards = document.querySelectorAll("#tags-view .tag-card");
    expect(cards.length).toBe(2);
    expect(cards[0].textContent).toContain("birthday");
    expect(cards[0].textContent).toContain("12 photos");
    // Cover img for the tag that has one; placeholder for the one that doesn't.
    expect(cards[0].querySelector("img")?.getAttribute("src")).toContain("/thumb/abc");
    expect(cards[1].querySelector(".tag-card-cover-empty")).not.toBeNull();
    // Cards are clickable + right-clickable.
    expect(cards[0].getAttribute("data-action")).toBe("navigateToTagPhotos");
    expect(cards[0].getAttribute("data-oncontextmenu")).toBe("_tagCardCtxMenu");
  });

  test("empty state when no tags", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ tags: [] });
    await loadTagsList();
    showTagsView();
    expect(document.getElementById("tags-view")?.textContent).toContain("No Tags Yet");
  });
});

describe("navigateToTagPhotos", () => {
  test("renders full cards into #photo-grid, parks vgrid, sets currentGridItems", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      tag: { id: 1, name: "birthday" },
      photos: [
        { filepath: "/a.jpg", filename: "a.jpg", thumb_hash: "h1" },
        { filepath: "/b.jpg", filename: "b.jpg", thumb_hash: "h2" },
      ],
    });
    await navigateToTagPhotos(1);
    const grid = document.getElementById("photo-grid");
    expect(grid?.classList.contains("simple-cards")).toBe(true);
    expect(grid?.querySelectorAll(".card").length).toBe(2);
    // Lightbox contract: the flat list IS what prev/next walks.
    expect(/** @type {any} */ (window).currentGridItems.length).toBe(2);
    // vgrid parked so its render loop can't clobber the cards.
    expect(/** @type {any} */ (window).vgrid.items.length).toBe(0);
  });
});
