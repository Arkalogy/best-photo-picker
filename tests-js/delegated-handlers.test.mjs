// @ts-check
/**
 * Handlers reached through the delegated dispatchers in globals.js get
 * what the dispatcher passes, not what a direct caller would: `(value,
 * event)` for input, `(event)` for mousedown and toggle, with the
 * element as `this`. These pin the handlers that read their arguments
 * the other way and silently did nothing.
 */
import { readFileSync, readdirSync } from "node:fs";

import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", async (importOriginal) => ({
  ...(await importOriginal()),
  apiFetch: vi.fn(),
}));

import { _resetYearMonths, loadYearMonths } from "../bpp/web/static/js/modules/albums-menus.mjs";
import { renderAlbumNav } from "../bpp/web/static/js/modules/albums.mjs";
import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { dismissConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";
import { _editorSliderChange } from "../bpp/web/static/js/modules/editor-tools.mjs";
import { eventHandler } from "../bpp/web/static/js/modules/event-handlers.mjs";
import { _getPetCtxClusterId, _petRowCtxMenu } from "../bpp/web/static/js/modules/pets.mjs";
import { pickTagSuggestion } from "../bpp/web/static/js/modules/tags.mjs";
import { onToolbarKKeydown } from "../bpp/web/static/js/modules/toolbar.mjs";
import { setup as albumsSetup, teardown as albumsTeardown } from "./_albums-setup.mjs";
import { setup as editorSetup } from "./_editor-setup.mjs";

const win = /** @type {any} */ (window);
const fetchMock = /** @type {import("vitest").Mock} */ (/** @type {unknown} */ (apiFetch));

beforeAll(() => {
  win.eventHandler = eventHandler;
  // eslint-disable-next-line no-new-func
  new Function("window", "document", readFileSync("bpp/web/static/js/globals.js", "utf8"))(
    window,
    document
  );
});

beforeEach(() => {
  fetchMock.mockReset();
});

describe("editor slider (input dispatcher)", () => {
  beforeEach(editorSetup);

  test("dragging a slider changes that adjustment", () => {
    win.editorEdits = { brightness: 1 };
    document.body.insertAdjacentHTML(
      "beforeend",
      '<input type="range" id="es-brightness" min="0.2" max="2" step="0.01" data-oninput="_editorSliderChange" data-slider-key="brightness">'
    );
    win._editorSliderChange = _editorSliderChange;
    const slider = /** @type {HTMLInputElement} */ (document.getElementById("es-brightness"));
    slider.value = "1.5";
    slider.dispatchEvent(new Event("input", { bubbles: true }));
    expect(win.editorEdits.brightness).toBe(1.5);
    expect(Object.keys(win.editorEdits)).not.toContain("1.5");
    delete win._editorSliderChange;
  });

  test("a direct call with (key, value) still works", () => {
    win.editorEdits = { contrast: 1 };
    _editorSliderChange("contrast", "1.2");
    expect(win.editorEdits.contrast).toBe(1.2);
  });
});

describe("tag suggestion (mousedown dispatcher)", () => {
  afterEach(() => {
    delete win.pickTagSuggestion;
    delete win.currentGridItems;
    delete win.lightboxIdx;
  });

  test("picking a suggestion adds that tag to the open photo", async () => {
    fetchMock.mockResolvedValue({ tags: [] });
    win.currentGridItems = [{ id: 5 }];
    win.lightboxIdx = 0;
    win.pickTagSuggestion = pickTagSuggestion;
    document.body.innerHTML =
      '<div class="lb-tag-option" data-onmousedown="pickTagSuggestion" data-arg0="2024">2024</div>';
    /** @type {HTMLElement} */ (document.querySelector(".lb-tag-option")).dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true })
    );
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/photos/5/tags");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "2024" });
  });

  test("a right-click on a suggestion adds nothing", async () => {
    win.currentGridItems = [{ id: 5 }];
    win.lightboxIdx = 0;
    win.pickTagSuggestion = pickTagSuggestion;
    document.body.innerHTML =
      '<div class="lb-tag-option" data-onmousedown="pickTagSuggestion" data-arg0="beach">beach</div>';
    /** @type {HTMLElement} */ (document.querySelector(".lb-tag-option")).dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, button: 2 })
    );
    await Promise.resolve();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("Timeline year (toggle dispatcher)", () => {
  beforeEach(_resetYearMonths);

  afterEach(() => {
    delete win.loadYearMonths;
  });

  test("opening a year loads its months", async () => {
    fetchMock.mockResolvedValue({ months: [{ month: 3, count: 7 }] });
    win.loadYearMonths = loadYearMonths;
    document.body.innerHTML =
      '<details class="nav-year-folder" data-year="2024" data-album-id="9" data-ontoggle="loadYearMonths">' +
      '<summary>2024</summary><div class="nav-year-months"></div></details>';
    const details = /** @type {HTMLDetailsElement} */ (document.querySelector("details"));
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    await vi.waitFor(() =>
      expect(details.querySelector(".nav-year-months")?.children.length).toBe(1)
    );
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/albums/time/months?year=2024");
  });

  test("a rebuilt sidebar shows the months at once and does not ask again", async () => {
    fetchMock.mockResolvedValue({ months: [{ month: 3, count: 7 }] });
    win.loadYearMonths = loadYearMonths;
    const html =
      '<details class="nav-year-folder" data-year="2024" data-album-id="9" data-ontoggle="loadYearMonths">' +
      '<summary>2024</summary><div class="nav-year-months"></div></details>';
    const open = () => {
      document.body.innerHTML = html;
      const details = /** @type {HTMLDetailsElement} */ (document.querySelector("details"));
      details.open = true;
      details.dispatchEvent(new Event("toggle"));
      return details;
    };
    const first = open();
    await vi.waitFor(() =>
      expect(first.querySelector(".nav-year-months")?.children.length).toBe(1)
    );
    const second = open();
    expect(second.querySelector(".nav-year-months")?.children.length).toBe(1); // no empty flash
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("pick-count box (keydown dispatcher)", () => {
  afterEach(() => {
    delete win.onToolbarKKeydown;
  });

  test("Enter commits the value by leaving the box", () => {
    win.onToolbarKKeydown = onToolbarKKeydown;
    document.body.innerHTML =
      '<input id="toolbar-k" type="number" data-onkeydown="onToolbarKKeydown">';
    const input = /** @type {HTMLInputElement} */ (document.getElementById("toolbar-k"));
    input.focus();
    expect(document.activeElement).toBe(input);
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    input.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).not.toBe(input);
  });
});

describe("confirm dialog", () => {
  test("Esc or a backdrop click cancels it, but not while its work is still running", () => {
    document.body.innerHTML =
      '<div id="confirm-overlay" class="visible"><button disabled id="install-close-btn">Close</button></div>';
    const overlay = /** @type {HTMLElement} */ (document.getElementById("confirm-overlay"));
    dismissConfirm();
    expect(overlay.classList.contains("visible")).toBe(true);
    /** @type {HTMLButtonElement} */ (document.getElementById("install-close-btn")).disabled =
      false;
    dismissConfirm();
    expect(overlay.classList.contains("visible")).toBe(false);
  });
});

describe("dialog backdrops (click dispatcher)", () => {
  const overlays = [
    ...readFileSync("bpp/web/templates/index.html", "utf8").matchAll(
      /<div[^>]*data-action="_bppBackdropClose"[^>]*>/g
    ),
  ].map((m) => m[0]);

  test("every overlay passes the click to the handler", () => {
    expect(overlays.length).toBeGreaterThan(5);
    for (const tag of overlays) expect(tag, tag).toContain("data-pass-event");
  });

  /** The app's own overlay tag, with a dialog inside. */
  function mount() {
    const tag = /** @type {string} */ (
      overlays.find((t) => t.includes('data-backdrop-fn="hideSettings"'))
    );
    document.body.innerHTML = `${tag}<div class="dialog"><input id="inside"></div></div>`;
    win.hideSettings = vi.fn();
    return /** @type {HTMLElement} */ (document.getElementById("settings-overlay"));
  }

  afterEach(() => {
    delete win.hideSettings;
  });

  test("a click on the backdrop closes the dialog", () => {
    const overlay = mount();
    overlay.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    overlay.click();
    expect(win.hideSettings).toHaveBeenCalledTimes(1);
  });

  test("a click inside the dialog does not", () => {
    mount();
    /** @type {HTMLElement} */ (document.getElementById("inside")).click();
    expect(win.hideSettings).not.toHaveBeenCalled();
  });

  test("a drag that starts inside the dialog and ends on the backdrop does not", () => {
    const overlay = mount();
    /** @type {HTMLElement} */ (document.getElementById("inside")).dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true })
    );
    overlay.click(); // the browser sends the click to the common ancestor
    expect(win.hideSettings).not.toHaveBeenCalled();
  });
});

describe("sidebar person and pet rows (contextmenu dispatcher)", () => {
  beforeEach(() => {
    albumsSetup();
    win.showPersonCtxMenu = vi.fn();
    win.albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 50 },
      { id: 41, album_type: "smart_person", name: "Ann", photo_count: 9, rule: { cluster_id: 3 } },
      {
        id: 42,
        album_type: "smart_pet",
        name: "Rex",
        photo_count: 9,
        rule: { cluster_id: 6, pet_class: "dog" },
      },
    ];
    win.faceClusters = [
      { cluster_id: 3, photo_count: 9, representative: { thumb_hash: "ab", face_index: 0 } },
    ];
    win.petClusters = [{ cluster_id: 6, photo_count: 9, name: "Rex" }];
    win.personDisplayName = (/** @type {number} */ cid) => (cid === 3 ? "Ann" : null);
  });

  afterEach(() => {
    albumsTeardown();
    for (const k of [
      "showPersonCtxMenu",
      "showPetCtxMenu",
      "faceClusters",
      "petClusters",
      "personDisplayName",
    ])
      delete win[k];
  });

  test("right-click on a person opens that person's menu, not the album id's", () => {
    renderAlbumNav();
    const row = /** @type {HTMLElement} */ (document.querySelector('[data-album-id="41"]'));
    expect(row, "person row").toBeTruthy();
    row.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));
    expect(win.showPersonCtxMenu).toHaveBeenCalledTimes(1);
    expect(win.showPersonCtxMenu.mock.calls[0][1]).toBe(3);
  });

  test("right-click on a pet opens that pet's menu", () => {
    win._petRowCtxMenu = _petRowCtxMenu;
    renderAlbumNav();
    const row = /** @type {HTMLElement} */ (document.querySelector('[data-album-id="42"]'));
    expect(row, "pet row").toBeTruthy();
    expect(row.dataset.arg0).toBe("42"); // the click still opens the album
    document.body.insertAdjacentHTML("beforeend", '<div id="pet-ctx-menu" class="hidden"></div>');
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    row.dispatchEvent(event);
    expect(document.getElementById("pet-ctx-menu")?.classList.contains("hidden")).toBe(false);
    expect(_getPetCtxClusterId()).toBe(6);
    delete win._petRowCtxMenu;
  });
});

test("no tag in the page's markup repeats an argument attribute", () => {
  // The browser keeps the first of two attributes with one name, so the
  // second handler on the element silently gets the first one's value.
  const dir = "bpp/web/static/js/modules";
  const files = [...readdirSync(dir).map((f) => `${dir}/${f}`), "bpp/web/templates/index.html"];
  /** @type {string[]} */
  const repeated = [];
  for (const file of files) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(/<[a-zA-Z][^<>]*?>/gs)) {
      for (const attr of new Set(m[0].match(/\bdata-arg\d(?==)/g) || [])) {
        if (m[0].split(`${attr}=`).length > 2) {
          repeated.push(`${file}:${src.slice(0, m.index).split("\n").length} ${attr}`);
        }
      }
    }
  }
  expect(repeated).toEqual([]);
});
