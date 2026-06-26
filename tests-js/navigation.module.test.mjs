// @ts-check
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  getSavedNavState,
  saveNavState,
  updateBreadcrumbs,
} from "../bpp/web/static/js/modules/navigation.mjs";

beforeEach(() => {
  document.body.innerHTML = '<div id="toolbar-title"></div>';
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).lightboxIdx = -1;
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "currentView",
    "currentAlbumId",
    "currentViewId",
    "lightboxIdx",
    "currentGridItems",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
});

describe("saveNavState / getSavedNavState", () => {
  test("round-trips view + album + viewId", () => {
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).currentViewId = "alpha";
    saveNavState();
    expect(getSavedNavState()).toMatchObject({ view: "album", albumId: 5, viewId: "alpha" });
  });

  test("includes lightboxPath when lightbox is open", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg" }, { filepath: "/b.jpg" }];
    /** @type {any} */ (window).lightboxIdx = 1;
    saveNavState();
    expect(getSavedNavState()?.lightboxPath).toBe("/b.jpg");
  });

  test("getSavedNavState returns null when nothing saved", () => {
    expect(getSavedNavState()).toBeNull();
  });

  test("getSavedNavState returns null on corrupted JSON", () => {
    localStorage.setItem("bpp_nav", "not-json{{{");
    expect(getSavedNavState()).toBeNull();
  });
});

describe("updateBreadcrumbs", () => {
  test("renders plain title when no parent", () => {
    updateBreadcrumbs("Library");
    expect(document.getElementById("toolbar-title")?.textContent).toBe("Library");
    expect(document.querySelector(".bc-link")).toBeNull();
  });

  test("renders breadcrumb with parent link + separator", () => {
    updateBreadcrumbs("Vacation", "Albums", "switchToLibrary()");
    const html = document.getElementById("toolbar-title")?.innerHTML || "";
    expect(html).toContain("bc-link");
    expect(html).toContain("Albums");
    expect(html).toContain("Vacation");
    expect(html).toContain("bc-sep");
  });

  test("no-arg parent action → bare data-action, no data-arg0", () => {
    updateBreadcrumbs("Vacation", "Library", "switchToLibrary()");
    const link = document.querySelector(".bc-link");
    expect(link?.getAttribute("data-action")).toBe("switchToLibrary");
    expect(link?.hasAttribute("data-arg0")).toBe(false);
  });

  test("arg'd parent action → data-action + data-arg0 (the Faces crumb bug)", () => {
    // Regression: "navigateTo('people')" used to be dropped verbatim into
    // data-action, which the dispatcher (data-action="fn" data-arg0="v")
    // couldn't resolve — so clicking "Faces" did nothing.
    updateBreadcrumbs("Leo", "Faces", "navigateTo('people')");
    const link = document.querySelector(".bc-link");
    expect(link?.getAttribute("data-action")).toBe("navigateTo");
    expect(link?.getAttribute("data-arg0")).toBe("people");
    // The old broken value must NOT survive.
    const html = document.getElementById("toolbar-title")?.innerHTML || "";
    expect(html).not.toContain("navigateTo('people')");
  });

  test("double-quoted arg form also parses", () => {
    updateBreadcrumbs("Cats", "Pets", 'navigateTo("pets")');
    const link = document.querySelector(".bc-link");
    expect(link?.getAttribute("data-action")).toBe("navigateTo");
    expect(link?.getAttribute("data-arg0")).toBe("pets");
  });

  test("HTML-escapes title and parentLabel", () => {
    updateBreadcrumbs("<img>", "Albums & co", "x()");
    const html = document.getElementById("toolbar-title")?.innerHTML || "";
    expect(html).not.toContain("<img>");
    expect(html).toContain("&lt;img&gt;");
    expect(html).toContain("Albums &amp; co");
  });

  test("noop when toolbar-title element missing", () => {
    document.body.innerHTML = "";
    expect(() => updateBreadcrumbs("X")).not.toThrow();
  });
});

describe("boot restoration ordering (regression)", () => {
  // showTuningState() fires loadAlbumList() but does NOT await it.
  // If the bootstrap reads win.albumList before awaiting it, the saved
  // album lookup falls through to loadPhotosAndRecompute() and the user
  // lands on Library on every refresh — even though bpp_nav was correctly
  // written. Lock the ordering: between the @anchor:bootstrap:tuning-start
  // and @anchor:bootstrap:savednav-read markers in app.mjs, the code
  // must await win.loadAlbumList. Anchors are stable across reformats;
  // the prior version used indexOf("showTuningState()") which broke on
  // cosmetic edits.
  const appSrc = readFileSync(resolve(process.cwd(), "bpp/web/static/js/modules/app.mjs"), "utf8");

  test("bootstrap awaits loadAlbumList between the tuning-start and savednav-read anchors", () => {
    const startIdx = appSrc.indexOf("@anchor:bootstrap:tuning-start");
    const endIdx = appSrc.indexOf("@anchor:bootstrap:savednav-read");
    expect(
      startIdx,
      "missing @anchor:bootstrap:tuning-start in app.mjs — anchor was removed without removing this test"
    ).toBeGreaterThan(-1);
    expect(
      endIdx,
      "missing @anchor:bootstrap:savednav-read in app.mjs — anchor was removed without removing this test"
    ).toBeGreaterThan(-1);
    expect(startIdx).toBeLessThan(endIdx);

    const between = appSrc.slice(startIdx, endIdx);
    expect(
      between,
      "bootstrap must await win.loadAlbumList between the tuning-start and savednav-read anchors — see comment block in app.mjs"
    ).toMatch(/await\s+win\.loadAlbumList\??\.?\(\)/);
  });
});
