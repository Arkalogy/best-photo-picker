// @ts-check
/**
 * Protection F — safeRender boundary tests.
 *
 * What this pins
 * --------------
 * safeRender() must:
 *   1. Run the render fn on the happy path and leave it untouched.
 *   2. On render throw, replace the container's contents with the
 *      fallback "couldn't render" pill so the surface degrades
 *      gracefully instead of going blank.
 *   3. Survive a missing container (don't crash if the DOM element
 *      isn't on the page — happens during early init or after a
 *      view switch).
 *
 * The Jun-2 incident proved one render exception can blank a whole
 * surface. safeRender is the generic boundary; B's safeRenderNav is
 * a thin convenience wrapper around this for the sidebar.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _resetSectionLabelsForTests,
  _safeRenderReload,
  clearSectionError,
  getSectionError,
  registerSectionLabel,
  safeRender,
  safeRenderNav,
  wrapSectionLoader,
} from "../bpp/web/static/js/modules/sidebar-safety.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="photo-grid"></div>
    <div id="album-list"></div>
    <div id="missing-target"></div>
  `;
});

afterEach(() => {
  document.body.innerHTML = "";
  _resetSectionLabelsForTests();
});

describe("safeRender", () => {
  test("runs the render fn and returns when it succeeds", () => {
    const fn = vi.fn(() => {
      const c = document.getElementById("photo-grid");
      if (c) c.innerHTML = "<span>ok</span>";
    });
    safeRender("photo-grid", "Photo grid", fn);
    expect(fn).toHaveBeenCalled();
    expect(document.getElementById("photo-grid")?.innerHTML).toBe("<span>ok</span>");
  });

  test("substitutes the fallback pill when the render fn throws", () => {
    const fn = vi.fn(() => {
      throw new Error("simulated render crash");
    });
    safeRender("photo-grid", "Photo grid", fn);
    expect(fn).toHaveBeenCalled();
    const container = document.getElementById("photo-grid");
    expect(container).toBeTruthy();
    expect(container?.innerHTML).toContain("Photo grid couldn't render");
    expect(container?.innerHTML).toContain("Reload");
  });

  test("does not crash when the container id is missing from DOM", () => {
    // The early-init / view-switch race: render fires before the
    // surface's container has been created. Must not throw.
    const fn = vi.fn();
    expect(() => safeRender("not-in-dom", "Whatever", fn)).not.toThrow();
  });

  test("escapes the label so HTML metacharacters render as text", () => {
    // Today every caller passes a hardcoded literal, but the moment
    // a dynamic name (album title, person name) is plumbed in, an
    // un-escaped `<` would render as markup and a `"` could close
    // an attribute. Pin the escape so the regression can't sneak in.
    safeRender("photo-grid", `<script>alert("x")</script>`, () => {
      throw new Error("boom");
    });
    const container = document.getElementById("photo-grid");
    expect(container?.innerHTML).toContain('&lt;script&gt;alert("x")&lt;/script&gt;');
    // And make sure no real <script> element ended up in the DOM.
    expect(container?.querySelector("script")).toBeNull();
  });

  test("fallback pill carries data-action='_safeRenderReload'", () => {
    safeRender("photo-grid", "Inspector", () => {
      throw new Error("x");
    });
    const btn = document.querySelector("#photo-grid button[data-action='_safeRenderReload']");
    expect(btn).toBeTruthy();
  });
});

describe("safeRenderNav (convenience wrapper)", () => {
  test("targets #album-list", () => {
    safeRenderNav(() => {
      throw new Error("sidebar boom");
    });
    expect(document.getElementById("album-list")?.innerHTML).toContain("Sidebar couldn't render");
  });
});

describe("_safeRenderReload", () => {
  test("calls window.location.reload()", () => {
    const reload = vi.fn();
    // jsdom's window.location is read-only; stub via Object.defineProperty.
    Object.defineProperty(window, "location", {
      value: { reload },
      writable: true,
    });
    _safeRenderReload();
    expect(reload).toHaveBeenCalled();
  });
});

describe("registerSectionLabel (plugin extensibility — P-07)", () => {
  test("a plugin-registered section uses its registered label in the sentinel", async () => {
    // Plugin author registers a section at startup, then routes its
    // loader through wrapSectionLoader. The retry-pill message must
    // carry the plugin's display label, not the raw kebab-case id.
    registerSectionLabel("trips", "Trips");
    /** @type {any} */ (window).renderAlbumNav = () => {};
    const loader = async () => {
      throw new Error("api 500");
    };
    const ok = await wrapSectionLoader("trips", loader, { silent: true });
    expect(ok).toBe(false);
    const err = getSectionError("trips");
    expect(err?.message).toBe("Couldn't load Trips");
    clearSectionError("trips");
  });

  test("an unregistered section falls back to its id (no crash)", async () => {
    /** @type {any} */ (window).renderAlbumNav = () => {};
    const loader = async () => {
      throw new Error("nope");
    };
    await wrapSectionLoader("custom-thing", loader, { silent: true });
    const err = getSectionError("custom-thing");
    // No label registered → the id appears verbatim. Better than
    // crashing or silently dropping the message.
    expect(err?.message).toBe("Couldn't load custom-thing");
    clearSectionError("custom-thing");
  });

  test("re-registering overrides a prior label", () => {
    registerSectionLabel("trips", "Trips");
    registerSectionLabel("trips", "Adventures");
    // Test via wrapSectionLoader failure path — that's the only
    // place the label leaks out for assertion.
    /** @type {any} */ (window).renderAlbumNav = () => {};
    return wrapSectionLoader(
      "trips",
      async () => {
        throw new Error("x");
      },
      { silent: true }
    ).then(() => {
      expect(getSectionError("trips")?.message).toBe("Couldn't load Adventures");
      clearSectionError("trips");
    });
  });
});

describe("wrapSectionLoader", () => {
  test("returns true on success and clears any previous error", async () => {
    /** @type {any} */ (window).renderAlbumNav = vi.fn();
    const loader = vi.fn(async () => {});
    const ok = await wrapSectionLoader("faces", loader);
    expect(ok).toBe(true);
    expect(loader).toHaveBeenCalled();
    expect(getSectionError("faces")).toBeNull();
  });

  test("returns false on error, stashes a sentinel, re-renders nav", async () => {
    /** @type {any} */ (window).renderAlbumNav = vi.fn();
    const loader = vi.fn(async () => {
      throw new Error("api 500");
    });
    const ok = await wrapSectionLoader("faces", loader, { silent: true });
    expect(ok).toBe(false);
    const err = getSectionError("faces");
    expect(err).toBeTruthy();
    expect(err?.message).toContain("People");
    // renderAlbumNav called in the catch branch so the error pill appears.
    expect(/** @type {any} */ (window).renderAlbumNav).toHaveBeenCalled();
  });
});
