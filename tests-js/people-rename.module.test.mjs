// @ts-check
import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/people-merge.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  doMerge: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  appConfirm: vi.fn(),
}));
// renamePerson's success path calls these — stub so they don't hit network.
vi.mock("../bpp/web/static/js/modules/albums.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  loadAlbumList: vi.fn(async () => {}),
  renderAlbumNav: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people-view.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  showPeopleView: vi.fn(),
}));

import {
  personNameSuggestions,
  renamePerson,
  startPersonRename,
} from "../bpp/web/static/js/modules/people.mjs";
import { doMerge } from "../bpp/web/static/js/modules/people-merge.mjs";
import { appConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

beforeEach(() => {
  document.body.innerHTML = "";
  /** @type {any} */ (state).faceClusters = [{ cluster_id: 6, name: "", filepaths: [] }];
  /** @type {any} */ (state).albumList = [];
});

describe("startPersonRename element resolution", () => {
  test("dispatched form (this = clicked element, no el arg) renders the input", () => {
    const div = document.createElement("div");
    div.textContent = "Person 6";
    document.body.appendChild(div);
    // Dispatcher calls fn.apply(el, args) → `this` is the clicked label, no
    // explicit el. Must use `this` and replace its content with the input.
    startPersonRename.call(div, 6);
    expect(div.querySelector("input.inline-rename-input")).not.toBeNull();
  });

  test("string el (the old broken 'this.parentElement' arg) does not throw", () => {
    const div = document.createElement("div");
    document.body.appendChild(div);
    // Regression: the grid used to pass the literal STRING "this.parentElement";
    // startPersonRename then did `el.innerHTML = ""` on a string and threw.
    // Now a non-Element el falls back to `this` (the dispatched element).
    expect(() => startPersonRename.call(div, 6, "this.parentElement")).not.toThrow();
    expect(div.querySelector("input.inline-rename-input")).not.toBeNull();
  });

  test("explicit element arg (ctx-menu / lightbox callers) is used", () => {
    const label = document.createElement("div");
    document.body.appendChild(label);
    // Direct callers pass a real element and no meaningful `this`.
    startPersonRename(6, label);
    expect(label.querySelector("input.inline-rename-input")).not.toBeNull();
  });

  test("no valid element anywhere → no-op, no throw", () => {
    expect(() => startPersonRename(6, "this.parentElement")).not.toThrow();
  });
});

const PEOPLE_ALBUMS = [
  { id: 1, album_type: "smart_person", name: "Rita", rule: { cluster_id: 3 } },
  { id: 2, album_type: "smart_person", name: "Person 7", rule: { cluster_id: 7 } },
  { id: 3, album_type: "smart_person", name: "Leo", rule: { cluster_id: 9 } },
  { id: 4, album_type: "smart_person", name: "Person 6", rule: { cluster_id: 6 } },
  { id: 5, album_type: "all", name: "Library" },
];

describe("personNameSuggestions", () => {
  test("returns real names sorted, skips 'Person N' placeholders + own cluster", () => {
    /** @type {any} */ (state).albumList = PEOPLE_ALBUMS;
    expect(personNameSuggestions(6)).toEqual(["Leo", "Rita"]);
    // Excluding Rita's own cluster drops her from her own suggestions.
    expect(personNameSuggestions(3)).toEqual(["Leo"]);
  });
});

describe("rename autocomplete dropdown", () => {
  /** @returns {HTMLInputElement} */
  function openRenameInput() {
    /** @type {any} */ (state).albumList = PEOPLE_ALBUMS;
    const div = document.createElement("div");
    document.body.appendChild(div);
    startPersonRename(6, div);
    return /** @type {HTMLInputElement} */ (div.querySelector("input.inline-rename-input"));
  }

  beforeEach(() => {
    document.body.innerHTML = "";
    document.querySelectorAll(".person-suggest").forEach((b) => b.remove());
  });

  test("typing filters the styled dropdown to matching names", () => {
    const input = openRenameInput();
    input.value = "ri";
    input.dispatchEvent(new Event("input"));
    const items = [...document.querySelectorAll(".person-suggest-item")].map((i) => i.textContent);
    expect(items).toEqual(["Rita"]);
  });

  test("focus with empty value shows all suggestions", () => {
    const input = openRenameInput();
    input.value = "";
    input.dispatchEvent(new Event("focus"));
    const items = [...document.querySelectorAll(".person-suggest-item")].map((i) => i.textContent);
    expect(items).toEqual(["Leo", "Rita"]);
  });

  test("mousedown on a suggestion fills the input and commits (one blur)", () => {
    const input = openRenameInput();
    input.value = "ri";
    input.dispatchEvent(new Event("input"));
    const item = /** @type {HTMLElement} */ (document.querySelector(".person-suggest-item"));
    item.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    expect(input.value).toBe("Rita");
    // Dropdown is gone immediately on pick.
    expect(document.querySelector(".person-suggest")).toBeNull();
  });

  test("ArrowDown highlights, Enter fills the highlighted name", () => {
    const input = openRenameInput();
    input.value = "";
    input.dispatchEvent(new Event("focus"));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    expect(document.querySelector(".person-suggest-item.active")?.textContent).toBe("Leo");
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(input.value).toBe("Leo");
  });
});

describe("renamePerson merge-on-duplicate", () => {
  beforeEach(() => {
    /** @type {any} */ (state).albumList = PEOPLE_ALBUMS;
    vi.mocked(doMerge).mockClear();
    vi.mocked(appConfirm).mockReset();
  });

  test("typing an existing name offers merge; confirm → doMerge(existing, [renamed])", async () => {
    vi.mocked(appConfirm).mockResolvedValue(true);
    await renamePerson(6, "rita"); // case-insensitive match
    expect(appConfirm).toHaveBeenCalledOnce();
    expect(String(vi.mocked(appConfirm).mock.calls[0][0])).toContain("Rita");
    // Existing person (cluster 3) is primary; the renamed cluster merges in.
    expect(doMerge).toHaveBeenCalledWith(3, [6]);
  });

  test("decline → no merge, no rename", async () => {
    vi.mocked(appConfirm).mockResolvedValue(false);
    await renamePerson(6, "Rita");
    expect(doMerge).not.toHaveBeenCalled();
  });
});

describe("renamePerson — success contract + optimistic revert (review CR)", () => {
  beforeEach(() => {
    /** @type {any} */ (state).albumList = [
      { id: 50, album_type: "smart_person", name: "", rule: { cluster_id: 6 } },
    ];
    /** @type {any} */ (state).faceClusters = [{ cluster_id: 6, name: "", filepaths: [] }];
  });

  test("returns true when the PUT persists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
      )
    );
    const ok = await renamePerson(6, "Leo");
    expect(ok).toBe(true);
  });

  test("returns false when the PUT fails (so callers can revert)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "nope" }), {
            status: 500,
            headers: { "content-type": "application/json" },
          })
      )
    );
    const ok = await renamePerson(6, "Leo");
    expect(ok).toBe(false);
  });

  test("lightbox rename reverts the chip label to the original on failure", async () => {
    const { startPersonRenameLightbox } = await import("../bpp/web/static/js/modules/people.mjs");
    document.body.innerHTML =
      '<div class="lb-face-chip" data-cluster-id="6"><span class="lb-face-name">Person 7</span></div>';
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "nope" }), {
            status: 500,
            headers: { "content-type": "application/json" },
          })
      )
    );
    startPersonRenameLightbox(6);
    const input = document.querySelector(".lb-face-name input");
    /** @type {any} */ (input).value = "Leo";
    input.dispatchEvent(new Event("blur"));
    // Let the async blur handler (await renamePerson) settle.
    await new Promise((r) => setTimeout(r, 0));
    expect(document.querySelector(".lb-face-name")?.textContent).toBe("Person 7");
  });
});
