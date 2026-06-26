// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _isSlideshowActive,
  _resetSlideshowState,
  _shuffleArray,
  _slideshowShow,
  slideshowNav,
  slideshowSetSpeed,
  slideshowToggleInfo,
  slideshowToggleKenBurns,
  slideshowTogglePlay,
  slideshowToggleShuffle,
  startSlideshow,
  stopSlideshow,
} from "../bpp/web/static/js/modules/slideshow.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="slideshow-overlay" class="hidden">
      <img id="ss-img-a" class="ss-img" />
      <img id="ss-img-b" class="ss-img" />
      <video id="ss-video" class="hidden"></video>
      <div id="ss-info"></div>
      <div id="ss-counter"></div>
      <button id="ss-play-btn"></button>
      <button id="ss-shuffle-btn"></button>
      <button id="ss-info-btn"></button>
      <button id="ss-kb-btn" class="active"></button>
      <button class="ss-speed-btn active" data-speed="3000"></button>
      <button class="ss-speed-btn" data-speed="5000"></button>
      <button class="ss-speed-btn" data-speed="8000"></button>
    </div>
  `;
  /** @type {any} */ (window).currentGridItems = [];
  // Make sure document.documentElement.requestFullscreen is a no-op spy
  /** @type {any} */ (document.documentElement).requestFullscreen = vi.fn();
  _resetSlideshowState();
});

afterEach(() => {
  _resetSlideshowState();
  document.body.innerHTML = "";
  vi.useRealTimers();
  delete (/** @type {any} */ (window).currentGridItems);
});

describe("_shuffleArray", () => {
  test("preserves length and elements", () => {
    const arr = [1, 2, 3, 4, 5];
    const orig = [...arr];
    _shuffleArray(arr);
    expect(arr).toHaveLength(5);
    expect(arr.sort()).toEqual(orig.sort());
  });

  test("with deterministic Math.random=0, produces a known order", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.0);
    const arr = [1, 2, 3, 4];
    _shuffleArray(arr);
    // Math.random()===0 → j=0 every iteration of Fisher-Yates:
    //   i=3, swap arr[3]↔arr[0] → [4,2,3,1]
    //   i=2, swap arr[2]↔arr[0] → [3,2,4,1]
    //   i=1, swap arr[1]↔arr[0] → [2,3,4,1]
    expect(arr).toEqual([2, 3, 4, 1]);
  });

  test("empty array no-ops", () => {
    /** @type {number[]} */
    const arr = [];
    expect(() => _shuffleArray(arr)).not.toThrow();
    expect(arr).toEqual([]);
  });
});

describe("startSlideshow / stopSlideshow", () => {
  test("toasts when grid is empty", () => {
    /** @type {any} */ (window).currentGridItems = [];
    startSlideshow();
    expect(_isSlideshowActive()).toBe(false);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "No photos to show"
    );
  });

  test("activates and unhides overlay with photos", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
      { filepath: "/b.jpg", thumb_hash: "hb", is_video: false },
    ];
    startSlideshow();
    expect(_isSlideshowActive()).toBe(true);
    expect(document.getElementById("slideshow-overlay")?.classList.contains("hidden")).toBe(false);
  });

  test("maps startIdx from currentGridItems via filepath", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
      { filepath: "/b.jpg", thumb_hash: "hb", is_video: false },
      { filepath: "/c.jpg", thumb_hash: "hc", is_video: false },
    ];
    startSlideshow(2);
    // After mapping idx=2 (filepath "/c.jpg") survives — counter shows "3 / 3"
    expect(document.getElementById("ss-counter")?.textContent).toBe("3 / 3");
  });

  test("stopSlideshow re-hides the overlay and clears active flag", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
    ];
    startSlideshow();
    stopSlideshow();
    expect(_isSlideshowActive()).toBe(false);
    expect(document.getElementById("slideshow-overlay")?.classList.contains("hidden")).toBe(true);
  });
});

describe("slideshowNav", () => {
  test("no-op when not active", () => {
    expect(() => slideshowNav(1)).not.toThrow();
  });

  test("wraps from last to first on +1", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
      { filepath: "/b.jpg", thumb_hash: "hb", is_video: false },
    ];
    startSlideshow(1);
    expect(document.getElementById("ss-counter")?.textContent).toBe("2 / 2");
    slideshowNav(1);
    expect(document.getElementById("ss-counter")?.textContent).toBe("1 / 2");
  });

  test("wraps from first to last on -1", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
      { filepath: "/b.jpg", thumb_hash: "hb", is_video: false },
    ];
    startSlideshow(0);
    slideshowNav(-1);
    expect(document.getElementById("ss-counter")?.textContent).toBe("2 / 2");
  });
});

describe("slideshowTogglePlay", () => {
  test("flips icon between play and pause", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
    ];
    startSlideshow();
    const btn = /** @type {HTMLElement} */ (document.getElementById("ss-play-btn"));
    // After startSlideshow, slideshowPlaying is true. Toggling makes it false → ssPlay.
    // jsdom normalizes <path/> to <path></path> when reading innerHTML, so we
    // assert on a structural marker instead of the raw SVG string.
    slideshowTogglePlay();
    expect(btn.innerHTML).toContain('d="M4 2.5v11l9-5.5z"'); // play icon
    slideshowTogglePlay();
    expect(btn.innerHTML).toContain('rect x="3"'); // pause icon (twin rects)
  });
});

describe("slideshowSetSpeed", () => {
  test("toggles active class on the matching speed button", () => {
    slideshowSetSpeed(5000);
    const btns = document.querySelectorAll(".ss-speed-btn");
    expect(btns[0].classList.contains("active")).toBe(false); // 3000
    expect(btns[1].classList.contains("active")).toBe(true); // 5000
    expect(btns[2].classList.contains("active")).toBe(false); // 8000
  });
});

describe("slideshowToggleShuffle", () => {
  test("toggles active class on shuffle button", () => {
    const btn = /** @type {HTMLElement} */ (document.getElementById("ss-shuffle-btn"));
    expect(btn.classList.contains("active")).toBe(false);
    slideshowToggleShuffle();
    expect(btn.classList.contains("active")).toBe(true);
    slideshowToggleShuffle();
    expect(btn.classList.contains("active")).toBe(false);
  });
});

describe("slideshowToggleInfo", () => {
  test("toggles active class on info button", () => {
    const btn = /** @type {HTMLElement} */ (document.getElementById("ss-info-btn"));
    slideshowToggleInfo();
    expect(btn.classList.contains("active")).toBe(true);
    slideshowToggleInfo();
    expect(btn.classList.contains("active")).toBe(false);
  });
});

describe("slideshowToggleKenBurns", () => {
  test("toggles active class on kb button", () => {
    const btn = /** @type {HTMLElement} */ (document.getElementById("ss-kb-btn"));
    expect(btn.classList.contains("active")).toBe(true);
    slideshowToggleKenBurns();
    expect(btn.classList.contains("active")).toBe(false);
    slideshowToggleKenBurns();
    expect(btn.classList.contains("active")).toBe(true);
  });

  test("when disabled, removes ss-kb-* classes from img layers", () => {
    const imgA = /** @type {HTMLElement} */ (document.getElementById("ss-img-a"));
    imgA.classList.add("ss-kb-2");
    slideshowToggleKenBurns(); // turns off
    expect(imgA.classList.contains("ss-kb-2")).toBe(false);
  });
});

describe("_slideshowShow", () => {
  test("ignores out-of-range index without throwing", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg", thumb_hash: "ha", is_video: false },
    ];
    startSlideshow();
    expect(() => _slideshowShow(99)).not.toThrow();
    expect(() => _slideshowShow(-1)).not.toThrow();
  });
});

describe("ICONS injection", () => {
  test("module attaches slideshow icons to window.ICONS", () => {
    const icons = /** @type {any} */ (window).ICONS;
    expect(icons.ssPlay).toContain("svg");
    expect(icons.ssPause).toContain("svg");
    expect(icons.ssShuffle).toContain("svg");
    expect(icons.ssInfo).toContain("svg");
    expect(icons.ssKenBurns).toContain("svg");
    expect(icons.ssSlideshow).toContain("svg");
  });
});
