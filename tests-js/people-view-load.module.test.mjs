// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// loadFaceClusters is the fetch the empty Faces view must call before it
// declares "No Faces Found". Mock it (and the heavy siblings) to isolate
// showPeopleView's load-then-render path.
const loadFaceClusters = vi.fn(() => Promise.resolve());
vi.mock("../bpp/web/static/js/modules/faces.mjs", () => ({ loadFaceClusters }));
vi.mock("../bpp/web/static/js/modules/utils.mjs", () => ({ hide: vi.fn(), show: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/core.mjs", () => ({ updateToolbarTitle: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({
  _selectedPeople: new Set(),
  personDisplayName: () => null,
}));
vi.mock("../bpp/web/static/js/modules/people-album-bar.mjs", () => ({
  setPersonAlbumClusterId: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people-actions.mjs", () => ({
  expandDismissedSection: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people-review.mjs", () => ({
  getAmbiguousPairCount: () => 0,
  refreshAmbiguousPairCount: vi.fn(),
}));

const { showPeopleView } = await import("../bpp/web/static/js/modules/people-view.mjs");
const { state } = await import("../bpp/web/static/js/modules/state.mjs");

beforeEach(() => {
  document.body.innerHTML = '<div class="content"></div><div id="status-summary"></div>';
  loadFaceClusters.mockClear();
  loadFaceClusters.mockResolvedValue(undefined);
  /** @type {any} */ (state).faceClusters = [];
  /** @type {any} */ (state).faceRecognitionAvailable = true;
  /** @type {any} */ (state).overrides = {};
});

afterEach(() => {
  document.body.innerHTML = "";
});

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("Faces view: load before declaring empty", () => {
  test("empty in-memory list shows Loading and fetches before declaring empty", async () => {
    showPeopleView();
    const view = document.getElementById("people-view");
    // Synchronous: loading shown, NOT the misleading empty state.
    expect(view?.textContent).toContain("Loading faces");
    expect(view?.textContent).not.toContain("No Faces Found");
    // The fetch fires on the dynamic-import microtask chain.
    await tick();
    expect(loadFaceClusters).toHaveBeenCalledTimes(1);
  });

  test("with faces present, afterLoad render shows the grid, not the empty state", () => {
    /** @type {any} */ (state).faceClusters = [
      {
        cluster_id: 0,
        photo_count: 5,
        filepaths: ["a.jpg"],
        representative: { thumb_hash: "abc123", face_index: 0 },
      },
    ];
    showPeopleView(true); // afterLoad path with data → full render
    const view = document.getElementById("people-view");
    expect(view?.textContent).not.toContain("No Faces Found");
    expect(view?.textContent).not.toContain("Loading faces");
  });

  test("afterLoad + genuinely empty → shows 'No Faces Found' (no refetch loop)", () => {
    showPeopleView(true);
    const view = document.getElementById("people-view");
    expect(view?.textContent).toContain("No Faces Found");
    expect(loadFaceClusters).not.toHaveBeenCalled();
  });

  test("face recognition unavailable → install hint, no fetch attempt", () => {
    /** @type {any} */ (state).faceRecognitionAvailable = false;
    showPeopleView();
    const view = document.getElementById("people-view");
    expect(view?.textContent).toContain("No Faces Found");
    expect(loadFaceClusters).not.toHaveBeenCalled();
  });
});
