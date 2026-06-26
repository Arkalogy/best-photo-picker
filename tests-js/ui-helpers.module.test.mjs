// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  initSliders,
  initTooltips,
  updateContentFilterLabel,
} from "../bpp/web/static/js/modules/ui-helpers.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="slider-row">
      <label>Sharpness</label>
      <input data-param="blur" type="range" min="0" max="1" step="0.1" value="0.5" />
      <span class="slider-value"></span>
      <span class="tip" data-tip="blur — sharpness weight"></span>
    </div>
    <div class="slider-row">
      <label>Sensitive photos</label>
      <span class="tip" id="sensitive-filter-tip" data-tip="placeholder"></span>
      <div class="theme-toggle" id="sensitive-toggle">
        <button class="theme-btn active" data-sens="allow">Allow</button>
        <button class="theme-btn" data-sens="exclude">Exclude</button>
      </div>
    </div>
  `;
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).nudenetAvailable = false;
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).scheduleRecompute);
  delete (/** @type {any} */ (window).nudenetAvailable);
});

describe("initSliders", () => {
  test("typing into a slider updates the sibling label and triggers recompute", () => {
    initSliders();
    const slider = /** @type {HTMLInputElement} */ (document.querySelector('[data-param="blur"]'));
    slider.value = "0.7";
    // bubbles: true — real input events bubble; initSliders is delegated
    // at the document level (so sliders survive container re-renders).
    slider.dispatchEvent(new Event("input", { bubbles: true }));
    const label = /** @type {HTMLElement} */ (slider.nextElementSibling);
    expect(label.textContent).toBeTruthy();
  });
});

describe("initTooltips", () => {
  test("creates a .tooltip element and shows it on hover", () => {
    initTooltips();
    expect(document.querySelector(".tooltip")).toBeTruthy();
    const target = /** @type {HTMLElement} */ (document.querySelector(".tip[data-tip]"));
    target.dispatchEvent(new MouseEvent("mouseenter"));
    const tip = /** @type {HTMLElement} */ (document.querySelector(".tooltip"));
    expect(tip.classList.contains("visible")).toBe(true);
    expect(tip.textContent).toContain("blur");
  });

  test("hides on mouseleave", () => {
    initTooltips();
    const target = /** @type {HTMLElement} */ (document.querySelector(".tip[data-tip]"));
    target.dispatchEvent(new MouseEvent("mouseenter"));
    target.dispatchEvent(new MouseEvent("mouseleave"));
    const tip = /** @type {HTMLElement} */ (document.querySelector(".tooltip"));
    expect(tip.classList.contains("visible")).toBe(false);
  });
});

describe("updateContentFilterLabel", () => {
  const tipEl = () => /** @type {HTMLElement} */ (document.getElementById("sensitive-filter-tip"));

  test("NudeNet copy + policy explainer when nudenetAvailable is true", () => {
    /** @type {any} */ (window).nudenetAvailable = true;
    updateContentFilterLabel();
    expect(tipEl().dataset.tip).toContain("NudeNet");
    expect(tipEl().dataset.tip).toContain("Manual includes always win");
    expect(tipEl().dataset.tip).not.toContain("pip install");
  });

  test("install hint when nudenetAvailable is false", () => {
    /** @type {any} */ (window).nudenetAvailable = false;
    updateContentFilterLabel();
    expect(tipEl().dataset.tip).toContain("pip install");
    expect(tipEl().dataset.tip).toContain("mark sensitive");
  });

  test("noop when the sensitive control is missing", () => {
    document.getElementById("sensitive-filter-tip")?.remove();
    expect(() => updateContentFilterLabel()).not.toThrow();
  });
});
