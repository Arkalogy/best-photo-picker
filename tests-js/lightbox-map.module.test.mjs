// @ts-check
// Map Expand/Collapse in the lightbox info panel.
//
// Regression (2026-06-11): "Expand doesn't work". The toggle used to be a
// direct addEventListener on #lb-map-header, attached once when the Leaflet
// map was first created — the editor rebuilds the panel's innerHTML, so the
// fresh header node had no listener and the link was dead. The fix is
// data-action delegation, which survives any innerHTML rebuild.
import { beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";

import { toggleLightboxMap } from "../bpp/web/static/js/modules/lightbox-info.mjs";

function leafletMapStub() {
  const onoff = () => ({ enable: vi.fn(), disable: vi.fn() });
  return {
    dragging: onoff(),
    scrollWheelZoom: onoff(),
    doubleClickZoom: onoff(),
    touchZoom: onoff(),
    boxZoom: onoff(),
    keyboard: onoff(),
    addControl: vi.fn(),
    removeControl: vi.fn(),
    invalidateSize: vi.fn(),
  };
}

beforeEach(() => {
  document.body.innerHTML = `
    <div class="lb-map-container" id="lb-map-container">
      <div class="lb-map-header" id="lb-map-header" data-action="toggleLightboxMap">
        <span class="lb-map-toggle" id="lb-map-toggle">Expand</span>
      </div>
      <div id="lb-map" class="lb-map"></div>
    </div>`;
  /** @type {any} */ (globalThis.window)._lbLeafletMap = leafletMapStub();
  /** @type {any} */ (globalThis.window).L = { control: { zoom: () => ({}) } };
});

describe("toggleLightboxMap", () => {
  test("expand → collapse round trip updates class, label, and interactions", () => {
    const wrap = document.getElementById("lb-map-container");
    const toggle = document.getElementById("lb-map-toggle");
    /** @type {any} */ const map = /** @type {any} */ (globalThis.window)._lbLeafletMap;

    toggleLightboxMap();
    expect(wrap?.classList.contains("expanded")).toBe(true);
    expect(toggle?.textContent).toBe("Collapse");
    expect(map.dragging.enable).toHaveBeenCalled();

    toggleLightboxMap();
    expect(wrap?.classList.contains("expanded")).toBe(false);
    expect(toggle?.textContent).toBe("Expand");
    expect(map.dragging.disable).toHaveBeenCalled();
    // Zoom control lives on the map instance (created on expand, torn
    // down on collapse) — a module-cached control from a destroyed map
    // rendered dead +/− buttons (regression 2026-06-12).
    expect(map.addControl).toHaveBeenCalledTimes(1);
    expect(map.removeControl).toHaveBeenCalledTimes(1);
    expect(map._bppZoomControl).toBeNull();
  });

  test("no Leaflet map → no-op, no throw", () => {
    /** @type {any} */ (globalThis.window)._lbLeafletMap = null;
    expect(() => toggleLightboxMap()).not.toThrow();
    expect(document.getElementById("lb-map-container")?.classList.contains("expanded")).toBe(false);
  });
});

describe("Expand wiring is delegation, not a direct listener (regression)", () => {
  const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

  test("both panel templates carry data-action on the map header", () => {
    for (const rel of [
      "../bpp/web/templates/index.html",
      "../bpp/web/static/js/modules/editor-rendering.mjs",
    ]) {
      const src = read(rel);
      const header = src.split("\n").find((l) => l.includes('id="lb-map-header"'));
      expect(header, `${rel} must wire the map header via data-action`).toContain(
        'data-action="toggleLightboxMap"'
      );
    }
  });

  test("lightbox-info no longer attaches a one-shot click listener", () => {
    const src = read("../bpp/web/static/js/modules/lightbox-info.mjs");
    expect(src).not.toMatch(/addEventListener\(\s*["']click["']\s*,\s*toggleLightboxMap/);
  });
});
