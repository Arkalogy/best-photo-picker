// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _checkCloudPath,
  _getOnboardingLibPath,
  _getOnboardingStep,
  _onboardingBack,
  _onboardingFinish,
  _onboardingNext,
  _renderOnboardingStep,
  _resetOnboardingState,
  hideOnboarding,
  showOnboarding,
} from "../bpp/web/static/js/modules/onboarding.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="onboarding-overlay" class="hidden">
      <div id="onboarding-body"></div>
      <div id="onboarding-dots"></div>
    </div>
    <div id="toast-container"></div>
  `;
  _resetOnboardingState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).showImportModal);
});

const overlay = () => /** @type {HTMLElement} */ (document.getElementById("onboarding-overlay"));

describe("show/hideOnboarding", () => {
  test("show reveals overlay, resets step, accepts library path", () => {
    showOnboarding("/some/lib");
    expect(overlay().classList.contains("hidden")).toBe(false);
    expect(_getOnboardingStep()).toBe(0);
    expect(_getOnboardingLibPath()).toBe("/some/lib");
  });

  test("show with no path uses empty string default", () => {
    showOnboarding();
    expect(_getOnboardingLibPath()).toBe("");
  });

  test("hide adds .hidden class", () => {
    overlay().classList.remove("hidden");
    hideOnboarding();
    expect(overlay().classList.contains("hidden")).toBe(true);
  });

  test("show is no-op when overlay missing", () => {
    document.body.innerHTML = "";
    expect(() => showOnboarding()).not.toThrow();
  });
});

describe("_renderOnboardingStep", () => {
  test("step 0 renders Welcome with 'Get Started' button", () => {
    showOnboarding();
    const bodyText = document.getElementById("onboarding-body").textContent;
    // The welcome page rebuilt from the original three-text layout
    // into a hero/tagline/CTA layout: <h1>Best Photo Picker</h1> +
    // <p>Find your best shots in seconds — on your own device.</p>
    // + Get Started button. The headline is the product name, no
    // longer "Welcome to ...".
    expect(bodyText).toContain("Best Photo Picker");
    expect(bodyText).toContain("Find your best shots");
    expect(bodyText).toContain("Get Started");
  });

  test("step 0 welcome discloses the background update check", () => {
    showOnboarding();
    // The update-check disclosure moved into a Details expander
    // (collapsed by default), but the text still lives in the DOM
    // — Node.textContent includes hidden descendants.
    const bodyText = document.getElementById("onboarding-body").textContent;
    expect(bodyText).toContain("api.github.com");
    expect(bodyText).toContain("Settings → App");
  });

  test("step 1 renders Library Location with path input + cloud-warn", () => {
    _onboardingNext();
    expect(document.getElementById("onboarding-body").textContent).toContain(
      "Your Library Location"
    );
    expect(document.getElementById("onboarding-lib-path")).toBeTruthy();
  });

  test("step 2 renders Use Context selector with three radio cards", () => {
    // New step in the 4-step wizard (was 3 steps). Personal /
    // Research / Commercial, sourced from
    // use-context-options.mjs so the copy can't drift from the
    // Settings → Models surface.
    _onboardingNext();
    _onboardingNext();
    const bodyText = document.getElementById("onboarding-body").textContent;
    expect(bodyText).toContain("How will you use Best Photo Picker?");
    expect(bodyText).toContain("Personal");
    expect(bodyText).toContain("Research");
    expect(bodyText).toContain("Commercial");
  });

  test("step 3 renders Ready with import-now and skip buttons", () => {
    _onboardingNext();
    _onboardingNext();
    _onboardingNext();
    const bodyText = document.getElementById("onboarding-body").textContent;
    expect(bodyText).toContain("You're all set");
    expect(bodyText).toContain("Import Photos Now");
  });

  test("progress bar fills proportionally to the current step", () => {
    // Replaces the previous 3-dot row. The wizard now writes
    // <div class="onb-progress-bar"><div class="onb-progress-fill"
    // style="width: N%"></div></div> where N = (step+1)/4 * 100.
    showOnboarding();
    let fill = /** @type {HTMLElement | null} */ (
      document.querySelector("#onboarding-dots .onb-progress-fill")
    );
    expect(fill).not.toBeNull();
    expect(fill?.style.width).toBe("25%"); // (0+1) / 4 = 25%

    _onboardingNext();
    fill = /** @type {HTMLElement | null} */ (
      document.querySelector("#onboarding-dots .onb-progress-fill")
    );
    expect(fill?.style.width).toBe("50%"); // (1+1) / 4

    _onboardingNext();
    fill = /** @type {HTMLElement | null} */ (
      document.querySelector("#onboarding-dots .onb-progress-fill")
    );
    expect(fill?.style.width).toBe("75%"); // (2+1) / 4

    _onboardingNext();
    fill = /** @type {HTMLElement | null} */ (
      document.querySelector("#onboarding-dots .onb-progress-fill")
    );
    expect(fill?.style.width).toBe("100%"); // (3+1) / 4 = full
  });
});

describe("_onboardingNext / _onboardingBack", () => {
  test("next advances 0 → 1 → 2 → 3 and clamps at 3", () => {
    showOnboarding();
    expect(_getOnboardingStep()).toBe(0);
    _onboardingNext();
    expect(_getOnboardingStep()).toBe(1);
    _onboardingNext();
    expect(_getOnboardingStep()).toBe(2);
    _onboardingNext();
    expect(_getOnboardingStep()).toBe(3);
    _onboardingNext();
    expect(_getOnboardingStep()).toBe(3);
  });

  test("back retreats 2 → 1 → 0 and clamps at 0", () => {
    showOnboarding();
    _onboardingNext();
    _onboardingNext();
    _onboardingBack();
    expect(_getOnboardingStep()).toBe(1);
    _onboardingBack();
    expect(_getOnboardingStep()).toBe(0);
    _onboardingBack();
    expect(_getOnboardingStep()).toBe(0);
  });

  test("next on step 1 captures the input value", () => {
    showOnboarding();
    _onboardingNext(); // → step 1
    /** @type {HTMLInputElement} */ (document.getElementById("onboarding-lib-path")).value =
      "/custom/path";
    _onboardingNext(); // → step 2, should capture
    expect(_getOnboardingLibPath()).toBe("/custom/path");
  });
});

describe("_checkCloudPath", () => {
  test("shows the warning for known cloud-sync prefixes", () => {
    document.body.innerHTML += `<div id="onboarding-cloud-warn" style="display:none"></div>`;
    _checkCloudPath("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/Pictures");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("onboarding-cloud-warn")).style.display
    ).toBe("flex");
  });

  test("hides for plain local paths", () => {
    document.body.innerHTML += `<div id="onboarding-cloud-warn" style="display:flex"></div>`;
    _checkCloudPath("/Users/x/Pictures/Library");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("onboarding-cloud-warn")).style.display
    ).toBe("none");
  });

  test("matches Dropbox / OneDrive / Google Drive / Box / iCloud Drive", () => {
    document.body.innerHTML += `<div id="onboarding-cloud-warn" style="display:none"></div>`;
    const cloudPaths = [
      "/Users/x/Dropbox/Photos",
      "/Users/x/OneDrive/Photos",
      "/Users/x/Google Drive/Photos",
      "/Users/x/Box/Photos",
      "/Users/x/Library/CloudStorage/iCloud Drive/Pictures",
    ];
    for (const p of cloudPaths) {
      _checkCloudPath(p);
      expect(
        /** @type {HTMLElement} */ (document.getElementById("onboarding-cloud-warn")).style.display
      ).toBe("flex");
    }
  });

  test("no-op when warn element is missing", () => {
    expect(() => _checkCloudPath("/anything")).not.toThrow();
  });
});

describe("_onboardingFinish", () => {
  test("hides overlay; opens import modal when importNow=true", async () => {
    /** @type {any} */ (window).showImportModal = vi.fn();
    showOnboarding("/lib");
    _onboardingNext();
    _onboardingNext();
    await _onboardingFinish(true);
    expect(overlay().classList.contains("hidden")).toBe(true);
    expect(/** @type {any} */ (window).showImportModal).toHaveBeenCalled();
  });

  test("doesn't open import modal when importNow=false", async () => {
    /** @type {any} */ (window).showImportModal = vi.fn();
    showOnboarding("/lib");
    _onboardingNext();
    _onboardingNext();
    await _onboardingFinish(false);
    expect(/** @type {any} */ (window).showImportModal).not.toHaveBeenCalled();
  });

  test("importNow=true opens the import modal and does NOT show the empty-state panel", async () => {
    /** @type {any} */ (window).showImportModal = vi.fn();
    /** @type {any} */ (window).showEmptyLibrary = vi.fn();
    showOnboarding("/lib");
    _onboardingNext();
    _onboardingNext();
    await _onboardingFinish(true);
    expect(/** @type {any} */ (window).showImportModal).toHaveBeenCalled();
    // Import modal takes over — empty-state panel would compete for attention.
    expect(/** @type {any} */ (window).showEmptyLibrary).not.toHaveBeenCalled();
  });

  test("importNow=false reveals the welcome empty-state panel (skip-and-explore CTA)", async () => {
    /** @type {any} */ (window).showImportModal = vi.fn();
    /** @type {any} */ (window).showEmptyLibrary = vi.fn();
    showOnboarding("/lib");
    _onboardingNext();
    _onboardingNext();
    await _onboardingFinish(false);
    // The audited bug: skipping import used to leave the user on a
    // bare hidden grid with no next-step affordance. Now we reveal
    // the welcome panel (icon + Import CTA + 4-step graphic) so they
    // have something actionable to click.
    expect(/** @type {any} */ (window).showEmptyLibrary).toHaveBeenCalled();
    expect(/** @type {any} */ (window).showImportModal).not.toHaveBeenCalled();
  });

  test("when path didn't change, doesn't POST to /api/libraries", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    showOnboarding("/lib");
    _onboardingNext();
    _onboardingNext();
    // input value unchanged from initial
    await _onboardingFinish(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
