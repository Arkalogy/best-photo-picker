// @ts-nocheck
/**
 * Tests for the cross-realm state module.
 *
 * The contract is small but load-bearing: state is a Proxy that
 * aliases window, the schema is the source of truth for which keys
 * are part of the cross-module surface, and writes propagate so
 * legacy `window.X` readers and new `state.X` callers always see
 * the same value.
 */

import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { initState, isStateKey, state, stateKeys } from "../bpp/web/static/js/modules/state.mjs";

beforeEach(() => {
  // Wipe + re-init so each test gets fresh defaults
  for (const key of stateKeys()) {
    delete window[key];
  }
  initState();
});

afterEach(() => {
  for (const key of stateKeys()) {
    delete window[key];
  }
});

describe("state schema", () => {
  test("includes the major user-facing fields", () => {
    // Headline fields whose absence would break the codebase. Don't
    // enumerate all 60+ — pin the ones that are most-touched so a
    // refactor can't silently drop one.
    for (const key of [
      "photos",
      "selectedPaths",
      "favorites",
      "faceClusters",
      "currentAlbumId",
      "albumList",
      "currentView",
      "lightboxIdx",
      "editorEdits",
    ]) {
      expect(isStateKey(key)).toBe(true);
    }
  });

  test("isStateKey rejects unknown keys", () => {
    expect(isStateKey("nopeNotAField")).toBe(false);
    expect(isStateKey("")).toBe(false);
  });

  test("stateKeys() lists all schema keys", () => {
    const keys = stateKeys();
    expect(keys.length).toBeGreaterThan(50);
    expect(keys).toContain("photos");
    expect(keys).toContain("faceClusters");
  });
});

describe("state aliases window", () => {
  test("read state.X reflects window.X", () => {
    window.photos = [{ filepath: "/x.jpg" }];
    expect(state.photos).toEqual([{ filepath: "/x.jpg" }]);
  });

  test("write state.X updates window.X", () => {
    state.currentAlbumId = 42;
    expect(window.currentAlbumId).toBe(42);
  });

  test("write window.X reflects in state.X (legacy callers)", () => {
    window.currentView = "people";
    expect(state.currentView).toBe("people");
  });

  test("Set mutation through state is visible on window", () => {
    state.selectedPaths.add("/a.jpg");
    expect(window.selectedPaths.has("/a.jpg")).toBe(true);
  });

  test("Set mutation through window is visible on state", () => {
    window.selectedPaths.add("/b.jpg");
    expect(state.selectedPaths.has("/b.jpg")).toBe(true);
  });
});

describe("initState defaults", () => {
  test("creates Set defaults via factory", () => {
    expect(state.favorites).toBeInstanceOf(Set);
    expect(state.multiSelected).toBeInstanceOf(Set);
    expect(state.selectedPaths).toBeInstanceOf(Set);
    expect(state.selectedFaceIds).toBeInstanceOf(Set);
  });

  test("creates Array defaults via factory", () => {
    expect(state.photos).toEqual([]);
    expect(state.faceClusters).toEqual([]);
    expect(state.albumList).toEqual([]);
  });

  test("preserves existing window value when re-initialized", () => {
    window.currentAlbumId = 7;
    initState();
    expect(state.currentAlbumId).toBe(7);
  });

  test("seeds scalar defaults", () => {
    expect(state.lastMultiClickIdx).toBe(-1);
    expect(state.peopleFilter).toBe("included");
    expect(state.lbZoom).toBe(1);
    expect(state.LB_ZOOM_MAX).toBe(10);
  });
});

describe("Proxy semantics", () => {
  test("'in' operator works on schema keys", () => {
    expect("photos" in state).toBe(true);
    expect("notARealField" in state).toBe(false);
  });

  test("Object.keys returns schema keys", () => {
    const keys = Object.keys(state);
    expect(keys).toContain("photos");
    expect(keys.length).toBeGreaterThan(50);
  });
});
