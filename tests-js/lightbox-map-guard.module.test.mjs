// @ts-check
/**
 * A map library failure (blocked tiles, a broken CDN copy, a throw inside
 * Leaflet) must not take the lightbox down with it: the map area hides
 * and the photo stays usable.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { updateLightboxMap } from "../bpp/web/static/js/modules/lightbox-info.mjs";

beforeEach(() => {
  document.body.innerHTML = `<div id="lb-map-container"><div id="lb-map"></div></div>`;
  delete (/** @type {any} */ (window)._lbLeafletMap);
});

afterEach(() => {
  document.body.innerHTML = "";
  delete (/** @type {any} */ (window).L);
  delete (/** @type {any} */ (window)._lbLeafletMap);
});

describe("lightbox map setup", () => {
  test("a throwing map library hides the map and does not throw", () => {
    /** @type {any} */ (window).L = {
      map: () => {
        throw new Error("Map container not found");
      },
    };
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => updateLightboxMap({ exif: { gps_lat: 1, gps_lon: 2 } })).not.toThrow();
    expect(document.getElementById("lb-map-container")?.classList.contains("hidden")).toBe(true);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
