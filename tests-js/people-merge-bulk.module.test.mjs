// @ts-nocheck
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// Mock people-merge so the merge picker's bulk branch is observable without
// running the real network merge. _selectedPeople must stay a real Set the
// picker reads from; mergeSelected is the delegation target we assert on.
const _selectedPeople = new Set();
const mergeSelected = vi.fn(() => Promise.resolve());

vi.mock("../bpp/web/static/js/modules/people-merge.mjs", () => ({
  _selectedPeople,
  mergeSelected,
}));

const { showMergePicker } = await import("../bpp/web/static/js/modules/people-pickers.mjs");
const { showPersonCtxMenu } = await import("../bpp/web/static/js/modules/people-ctx-menu.mjs");
const { state } = await import("../bpp/web/static/js/modules/state.mjs");

beforeEach(() => {
  document.body.innerHTML = "";
  _selectedPeople.clear();
  mergeSelected.mockClear();
  /** @type {any} */ (state).faceClusters = [
    { cluster_id: 0, photo_count: 10, representative: null },
    { cluster_id: 1, photo_count: 5, representative: null },
    { cluster_id: 2, photo_count: 3, representative: null },
  ];
  // personDisplayName() resolves names through smart_person albums, not the
  // faceClusters rows.
  /** @type {any} */ (state).albumList = [
    { id: 100, album_type: "smart_person", name: "Alice", rule: { cluster_id: 0 } },
    { id: 101, album_type: "smart_person", name: "Bob", rule: { cluster_id: 1 } },
    { id: 102, album_type: "smart_person", name: "Carol", rule: { cluster_id: 2 } },
  ];
});

afterEach(() => {
  document.getElementById("merge-picker-overlay")?.remove();
});

describe("showMergePicker — bulk vs single", () => {
  test("single source (nothing selected) keeps the single-person header", () => {
    showMergePicker(0);
    const header = document.querySelector(".merge-picker-header");
    expect(header.textContent).toContain('Merge "Alice" into');
  });

  test("a lone selected source is NOT bulk (needs >1 selected)", () => {
    _selectedPeople.add(0);
    showMergePicker(0);
    const header = document.querySelector(".merge-picker-header");
    expect(header.textContent).toContain('Merge "Alice" into');
  });

  test("bulk: clicking a target merges the WHOLE selection, not just the source", () => {
    _selectedPeople.add(0); // Alice (the source)
    _selectedPeople.add(1); // Bob
    showMergePicker(0);

    const header = document.querySelector(".merge-picker-header");
    expect(header.textContent).toContain("Merge 2 people into");

    // Source (0) is excluded from the target list → Bob(1), Carol(2).
    const items = [...document.querySelectorAll(".merge-picker-item")];
    expect(items.length).toBe(2);

    const carol = items.find((it) => it.textContent.includes("Carol"));
    carol.click();

    // The fix: delegate to mergeSelected(target), which absorbs every
    // selected person — not the old doMerge(target, [sourceClusterId]).
    expect(mergeSelected).toHaveBeenCalledTimes(1);
    expect(mergeSelected).toHaveBeenCalledWith(2);
  });
});

function ctxEvent() {
  return {
    preventDefault() {},
    stopPropagation() {},
    clientX: 10,
    clientY: 10,
    target: { closest: () => null }, // → "lightbox" source; irrelevant here
  };
}

function buildCtxMenu() {
  const menu = document.createElement("div");
  menu.id = "person-ctx-menu";
  menu.classList.add("hidden");
  const merge = document.createElement("div");
  merge.dataset.action = "merge";
  menu.appendChild(merge);
  document.body.appendChild(menu);
  return menu;
}

describe("showPersonCtxMenu — bulk-merge item visibility", () => {
  test("hidden when nothing is selected", () => {
    const menu = buildCtxMenu();
    showPersonCtxMenu(ctxEvent(), 0);
    const item = menu.querySelector('[data-action="merge-selected"]');
    expect(item.style.display).toBe("none");
  });

  test("shown when right-clicking an UNselected target (others selected)", () => {
    _selectedPeople.add(0);
    _selectedPeople.add(1);
    const menu = buildCtxMenu();
    showPersonCtxMenu(ctxEvent(), 2); // Carol not selected
    const item = menu.querySelector('[data-action="merge-selected"]');
    expect(item.style.display).toBe("block");
    expect(item.textContent).toBe('Merge 2 selected into "Carol"');
  });

  test("shown when right-clicking a SELECTED target — the old bug (was hidden)", () => {
    _selectedPeople.add(0); // Alice, the right-clicked target
    _selectedPeople.add(1); // Bob
    const menu = buildCtxMenu();
    showPersonCtxMenu(ctxEvent(), 0);
    const item = menu.querySelector('[data-action="merge-selected"]');
    expect(item.style.display).toBe("block");
    // Count reflects the OTHER selected people absorbed, not the target.
    expect(item.textContent).toBe('Merge 1 selected into "Alice"');
  });
});
