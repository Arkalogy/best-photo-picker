// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _openSiblingCompare,
  disableClipCapOverride,
  enableClipCapOverride,
  openLightboxByPath,
  toggleClipCapLearnMore,
  updateClipCapBanner,
  updateClipStatus,
  updateClipStatusFromAppStatus,
  updateDedupStats,
  updateLightboxSimilar,
} from "../bpp/web/static/js/modules/clip.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="clip-status-row" class="hidden">
      <span id="clip-status-badge"></span>
      <span id="clip-status-desc">Description</span>
      <button id="btn-clip-extract">Compute</button>
      <div id="clip-progress" class="hidden">
        <span id="clip-progress-text"></span>
        <div id="clip-progress-fill"></div>
      </div>
    </div>
    <div id="dedup-adaptive-info" class="hidden">
      <span id="dedup-threshold-val"></span>
      <span id="dedup-feedback-count"></span>
    </div>
    <div id="hash-dedup-controls" style="opacity:1"></div>
    <div id="lb-similar" class="hidden">
      <span class="lb-similar-label"></span>
      <div id="lb-similar-strip"></div>
    </div>
    <div id="clip-cap-banner" class="hidden clip-cap-banner">
      <p id="clip-cap-msg"></p>
      <p id="clip-cap-learn" class="hidden"></p>
      <button id="btn-clip-cap-enable"></button>
      <button id="btn-clip-cap-learn"></button>
    </div>
    <div id="clip-cap-active" class="hidden">
      <span id="clip-cap-active-msg"></span>
      <button id="btn-clip-cap-disable"></button>
    </div>
    <div id="toast-container"></div>
  `;
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const badge = () => /** @type {HTMLElement} */ (document.getElementById("clip-status-badge"));
const btn = () => /** @type {HTMLButtonElement} */ (document.getElementById("btn-clip-extract"));

describe("updateClipStatus", () => {
  test("ready=true: 'Ready' badge, hides button, shows adaptive-info, dims hash controls", () => {
    updateClipStatus(true);
    expect(badge().className).toBe("clip-badge ready");
    expect(badge().textContent).toBe("Ready");
    expect(btn().classList.contains("hidden")).toBe(true);
    expect(document.getElementById("dedup-adaptive-info").classList.contains("hidden")).toBe(false);
    expect(
      /** @type {HTMLElement} */ (document.getElementById("hash-dedup-controls")).style.opacity
    ).toBe("0.4");
  });

  test("ready=false: 'Off' badge, shows description, hides adaptive-info, restores hash controls", () => {
    updateClipStatus(false);
    expect(badge().className).toBe("clip-badge off");
    expect(badge().textContent).toBe("Off");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("clip-status-desc")).style.display
    ).toBe("block");
    expect(document.getElementById("dedup-adaptive-info").classList.contains("hidden")).toBe(true);
    expect(
      /** @type {HTMLElement} */ (document.getElementById("hash-dedup-controls")).style.opacity
    ).toBe("1");
  });
});

describe("updateClipStatusFromAppStatus", () => {
  test("clip_ready + count > 0 → ready", () => {
    updateClipStatusFromAppStatus({ clip_ready: true, clip_embedding_count: 100 });
    expect(badge().textContent).toBe("Ready");
  });

  test("clip_extracting → 'Computing...'", () => {
    updateClipStatusFromAppStatus({ clip_extracting: true });
    expect(badge().textContent).toBe("Computing...");
    expect(btn().classList.contains("hidden")).toBe(true);
  });

  test("clip_available (idle) → 'Off' with button visible", () => {
    updateClipStatusFromAppStatus({ clip_available: true });
    expect(badge().textContent).toBe("Off");
    expect(btn().classList.contains("hidden")).toBe(false);
  });

  test("clip_installable → 'Not installed' with install-prompt button", () => {
    updateClipStatusFromAppStatus({ clip_installable: true });
    expect(badge().textContent).toBe("Not installed");
    expect(btn().textContent).toBe("Install CLIP extra");
  });
});

describe("updateDedupStats", () => {
  test("renders threshold + feedback count and reveals adaptive-info", () => {
    updateDedupStats({
      clip_threshold: 0.823,
      clip_threshold_info: { feedback_count: 5 },
    });
    expect(document.getElementById("dedup-threshold-val").textContent).toBe("0.823");
    expect(document.getElementById("dedup-feedback-count").textContent).toBe("5 feedback signals");
    expect(document.getElementById("dedup-adaptive-info").classList.contains("hidden")).toBe(false);
  });

  test("'1 feedback signal' singular form", () => {
    updateDedupStats({
      clip_threshold: 0.5,
      clip_threshold_info: { feedback_count: 1 },
    });
    expect(document.getElementById("dedup-feedback-count").textContent).toBe("1 feedback signal");
  });

  test("zero feedback → 'default'", () => {
    updateDedupStats({
      clip_threshold: 0.5,
      clip_threshold_info: { feedback_count: 0 },
    });
    expect(document.getElementById("dedup-feedback-count").textContent).toBe("default");
  });

  test("no clip_threshold → no-op", () => {
    updateDedupStats(/** @type {any} */ ({}));
    expect(document.getElementById("dedup-adaptive-info").classList.contains("hidden")).toBe(true);
  });
});

describe("updateLightboxSimilar", () => {
  test("hidden when similar_photos is empty/missing", () => {
    updateLightboxSimilar({});
    expect(document.getElementById("lb-similar").classList.contains("hidden")).toBe(true);
  });

  test("renders one .lb-similar-item per sibling with similarity %", () => {
    updateLightboxSimilar({
      similar_photos: [
        { thumb_hash: "h1", similarity: 0.95 },
        { thumb_hash: "h2", similarity: 0.82 },
      ],
    });
    const items = document.querySelectorAll(".lb-similar-item");
    expect(items).toHaveLength(2);
    expect(document.querySelector(".lb-similar-label").textContent).toContain("2 similar photos");
    expect(document.getElementById("lb-similar").classList.contains("hidden")).toBe(false);
  });

  test("singular 'similar photo' for 1 item", () => {
    updateLightboxSimilar({
      similar_photos: [{ thumb_hash: "h1", similarity: 0.99 }],
    });
    expect(document.querySelector(".lb-similar-label").textContent).toContain("1 similar photo");
  });

  test("Moment membership → one-line header + Review + all-shots strip", () => {
    updateLightboxSimilar({
      _isMoment: true,
      thumb_hash: "hme",
      similar_photos: [
        { thumb_hash: "h1", similarity: null },
        { thumb_hash: "h2", similarity: null },
      ],
    });
    const label = document.querySelector(".lb-similar-label");
    // 2 siblings + the open photo = a 3-shot Moment; quiet header + Review.
    expect(label.textContent).toContain("3-shot Moment");
    expect(label.textContent).not.toContain("NaN");
    expect(label.querySelector(".lb-moment-review")).not.toBeNull();
    // Compact strip: current photo leads (highlighted, not clickable),
    // all shots present, no similarity badges.
    const strip = document.getElementById("lb-similar-strip");
    expect(strip.classList.contains("lb-moment-strip")).toBe(true);
    const items = strip.querySelectorAll(".lb-similar-item");
    expect(items.length).toBe(3);
    expect(items[0].classList.contains("lb-moment-current")).toBe(true);
    expect(items[0].hasAttribute("data-action")).toBe(false);
    expect(strip.querySelectorAll(".lb-similar-pct").length).toBe(0);
  });

  test("non-Moment similar photos keep the badged grid (no moment classes)", () => {
    updateLightboxSimilar({
      similar_photos: [{ thumb_hash: "h1", similarity: 0.93 }],
    });
    const strip = document.getElementById("lb-similar-strip");
    expect(strip.classList.contains("lb-moment-strip")).toBe(false);
    expect(strip.querySelector(".lb-similar-pct").textContent).toBe("93%");
    expect(document.querySelector(".lb-similar-label").classList.contains("lb-moment-head")).toBe(
      false
    );
  });
});

describe("_openSiblingCompare", () => {
  test("no-op when lightboxIdx is unset", () => {
    /** @type {any} */ (window).openCompareWithSibling = vi.fn();
    _openSiblingCompare(0);
    expect(/** @type {any} */ (window).openCompareWithSibling).not.toHaveBeenCalled();
    delete (/** @type {any} */ (window).openCompareWithSibling);
  });

  test("forwards parent + siblings + index to openCompareWithSibling", () => {
    const parent = { similar_photos: [{ thumb_hash: "h1", similarity: 1 }] };
    /** @type {any} */ (window).lightboxIdx = 0;
    /** @type {any} */ (window).currentGridItems = [parent];
    /** @type {any} */ (window).openCompareWithSibling = vi.fn();
    _openSiblingCompare(0);
    expect(/** @type {any} */ (window).openCompareWithSibling).toHaveBeenCalledWith(
      parent,
      parent.similar_photos,
      0
    );
    delete (/** @type {any} */ (window).lightboxIdx);
    delete (/** @type {any} */ (window).currentGridItems);
    delete (/** @type {any} */ (window).openCompareWithSibling);
  });
});

describe("openLightboxByPath", () => {
  test("calls openLightbox with the matching index", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a.jpg" },
      { filepath: "/b.jpg" },
      { filepath: "/c.jpg" },
    ];
    /** @type {any} */ (window).openLightbox = vi.fn();
    openLightboxByPath("/b.jpg");
    expect(/** @type {any} */ (window).openLightbox).toHaveBeenCalledWith(1);
    delete (/** @type {any} */ (window).currentGridItems);
    delete (/** @type {any} */ (window).openLightbox);
  });

  test("no-op when filepath not found", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg" }];
    /** @type {any} */ (window).openLightbox = vi.fn();
    openLightboxByPath("/missing.jpg");
    expect(/** @type {any} */ (window).openLightbox).not.toHaveBeenCalled();
    delete (/** @type {any} */ (window).currentGridItems);
    delete (/** @type {any} */ (window).openLightbox);
  });
});

describe("updateClipCapBanner", () => {
  const banner = () => /** @type {HTMLElement} */ (document.getElementById("clip-cap-banner"));
  const active = () => /** @type {HTMLElement} */ (document.getElementById("clip-cap-active"));
  const msg = () => /** @type {HTMLElement} */ (document.getElementById("clip-cap-msg"));
  const activeMsg = () =>
    /** @type {HTMLElement} */ (document.getElementById("clip-cap-active-msg"));

  test("disabled_too_large → banner visible with peak-GB count, active line hidden", () => {
    updateClipCapBanner({
      clip_cap_status: "disabled_too_large",
      clip_cap: 200000,
      clip_cap_peak_mb: 1200,
      clip_embedding_count: 280000,
    });
    expect(banner().classList.contains("hidden")).toBe(false);
    expect(active().classList.contains("hidden")).toBe(true);
    expect(msg().textContent).toContain("280,000");
    expect(msg().textContent).toContain("1.2 GB");
  });

  test("enabled_override → active line visible with cap + peak, banner hidden", () => {
    updateClipCapBanner({
      clip_cap_status: "enabled_override",
      clip_cap: 200000,
      clip_cap_peak_mb: 1800,
      clip_embedding_count: 300000,
    });
    expect(banner().classList.contains("hidden")).toBe(true);
    expect(active().classList.contains("hidden")).toBe(false);
    expect(activeMsg().textContent).toContain("200,000");
    expect(activeMsg().textContent).toContain("1.8 GB");
  });

  test("enabled (under cap) → both hidden", () => {
    updateClipCapBanner({
      clip_cap_status: "enabled",
      clip_cap: 200000,
      clip_cap_peak_mb: 50,
      clip_embedding_count: 10000,
    });
    expect(banner().classList.contains("hidden")).toBe(true);
    expect(active().classList.contains("hidden")).toBe(true);
  });

  test("missing clip_cap_status field → both hidden (backward compat)", () => {
    // Older /api/v1/status responses won't have the new fields.
    // The banner must stay hidden rather than throwing.
    updateClipCapBanner({});
    expect(banner().classList.contains("hidden")).toBe(true);
    expect(active().classList.contains("hidden")).toBe(true);
  });
});

describe("toggleClipCapLearnMore", () => {
  test("toggles the inline learn-more block", () => {
    const learn = /** @type {HTMLElement} */ (document.getElementById("clip-cap-learn"));
    expect(learn.classList.contains("hidden")).toBe(true);
    toggleClipCapLearnMore();
    expect(learn.classList.contains("hidden")).toBe(false);
    toggleClipCapLearnMore();
    expect(learn.classList.contains("hidden")).toBe(true);
  });
});

describe("enableClipCapOverride / disableClipCapOverride", () => {
  /** @type {any} */
  let lastFetch;
  beforeEach(() => {
    lastFetch = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts) => {
        lastFetch = { url, opts };
        return new Response(
          JSON.stringify({
            clip_cap_status: "enabled_override",
            clip_cap: 200000,
            clip_cap_peak_mb: 1500,
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        );
      })
    );
  });

  test("enable: POSTs with {enable:true} and re-renders banner to enabled_override", async () => {
    await enableClipCapOverride();
    expect(lastFetch.url).toBe("/api/v1/settings/clip_max_override");
    expect(lastFetch.opts.method).toBe("POST");
    expect(JSON.parse(lastFetch.opts.body)).toEqual({ enable: true });
    const active = /** @type {HTMLElement} */ (document.getElementById("clip-cap-active"));
    expect(active.classList.contains("hidden")).toBe(false);
  });

  test("disable: POSTs with {enable:false}", async () => {
    await disableClipCapOverride();
    expect(lastFetch.url).toBe("/api/v1/settings/clip_max_override");
    expect(JSON.parse(lastFetch.opts.body)).toEqual({ enable: false });
  });

  test("fetch error: shows error toast, doesn't crash", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await enableClipCapOverride();
    const toast = document.querySelector("#toast-container .toast");
    expect(toast?.textContent).toContain("Couldn't save");
  });
});
