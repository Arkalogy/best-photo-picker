// @ts-check
/**
 * Every browse card (people, pets, groups, memories, on this day) is a
 * button to the keyboard: in the tab order, announced as a button, and
 * activated by Enter or Space through the data-action dispatcher's
 * key-activate path.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/people-pair-review.mjs", () => ({
  refreshAmbiguousPairCount: vi.fn(async () => {}),
  getAmbiguousPairCount: () => 0,
}));

import { showPeopleView } from "../bpp/web/static/js/modules/people-view.mjs";
import { showPetsView, _resetPetsState } from "../bpp/web/static/js/modules/pets.mjs";
import { _setFaceGroups, showGroupsView } from "../bpp/web/static/js/modules/groups.mjs";
import { _setMemoriesList, renderMemoriesSidebar } from "../bpp/web/static/js/modules/memories.mjs";
import { _setOnThisDayData, renderOnThisDay } from "../bpp/web/static/js/modules/on-this-day.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="content">
      <div id="toolbar"></div>
      <div id="status-bar"><span id="status-summary"></span></div>
      <div id="photo-grid"></div>
    </div>
    <div id="toolbar-title"></div><div id="toolbar-subtitle"></div>
    <div id="person-album-bar"></div><div id="person-photo-selection-bar"></div>
    <div id="memories-nav" class="hidden"></div>
    <div id="on-this-day" class="hidden"></div>
    <div id="toast-container"></div>`;
  const win = /** @type {any} */ (window);
  win.ICONS = { paw: "<i/>", people: "<i/>", group: "<i/>" };
  win.albumList = [];
  win.hide = vi.fn();
  win.show = vi.fn();
  win.updateToolbarTitle = vi.fn();
  win.faceRecognitionAvailable = true;
  win.petsAvailable = true;
  win.petClusters = [
    {
      cluster_id: 1,
      photo_count: 3,
      pet_class: "cat",
      representative: { thumb_hash: "ha", detection_index: 0 },
    },
  ];
  /** @type {any} */ (state).faceClusters = [
    {
      cluster_id: 1,
      name: "Kai",
      photo_count: 30,
      filepaths: [],
      representative: { thumb_hash: "h1", face_index: 0 },
    },
  ];
  /** @type {any} */ (state).albumList = [];
  /** @type {any} */ (state).peopleFilter = "all";
  _resetPetsState();
});

afterEach(() => {
  document.body.innerHTML = "";
});

/** @param {string} selector */
function expectButtonCard(selector) {
  const card = /** @type {HTMLElement | null} */ (document.querySelector(selector));
  expect(card, selector).not.toBeNull();
  const el = /** @type {HTMLElement} */ (card);
  expect(el.getAttribute("tabindex"), `${selector} tabindex`).toBe("0");
  expect(el.getAttribute("role"), `${selector} role`).toBe("button");
  expect(el.hasAttribute("data-key-activate"), `${selector} key-activate`).toBe(true);
  expect(el.hasAttribute("data-action"), `${selector} action`).toBe(true);
}

describe("browse cards are keyboard buttons", () => {
  test("people", () => {
    showPeopleView(true);
    expectButtonCard(".person-card[data-cluster-id]");
  });
  test("pets", () => {
    showPetsView();
    expectButtonCard(".pet-card");
  });
  test("groups", () => {
    _setFaceGroups([{ member_info: [{ name: "A" }, { name: "B" }], photo_count: 2 }]);
    showGroupsView();
    expectButtonCard(".group-card");
  });
  test("memories", () => {
    _setMemoriesList([{ id: 1, title: "Beach", photo_count: 4 }]);
    renderMemoriesSidebar();
    expectButtonCard(".memory-card");
  });
  test("on this day", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 4,
        day: 25,
        years: [{ year: 2024, years_ago: 1, count: 5, photos: [] }],
      })
    );
    renderOnThisDay();
    expectButtonCard(".otd-card");
  });
});
