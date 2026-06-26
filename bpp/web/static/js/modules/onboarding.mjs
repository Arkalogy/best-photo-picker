// @ts-check
/**
 * First-run onboarding flow — 3-step modal walking the user through
 * welcome → library location → ready/import. Triggered from
 * app.js's bootstrap when the server reports `first_run`.
 */

import { apiFetch } from "./api-client.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toastError } from "./toast.mjs";
import { USE_CONTEXT_OPTIONS } from "./use-context-options.mjs";

let _onboardingStep = 0;
let _onboardingLibPath = "";

export function _getOnboardingStep() {
  return _onboardingStep;
}

export function _getOnboardingLibPath() {
  return _onboardingLibPath;
}

export function _resetOnboardingState() {
  _onboardingStep = 0;
  _onboardingLibPath = "";
}

/** @param {string} [libraryPath] */
export function showOnboarding(libraryPath) {
  _onboardingStep = 0;
  _onboardingLibPath = libraryPath || "";
  const overlay = document.getElementById("onboarding-overlay");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  _renderOnboardingStep();
  // Esc dismisses to the same place the Skip button does — first-time
  // users reflexively press Esc to back out; trapping them is the
  // worst possible first impression. Capture phase + stopImmediate
  // matches the dialogs.mjs / export-modal pattern so Esc doesn't
  // bubble into lightbox or global handlers.
  document.addEventListener("keydown", _onOnboardingKey, true);
}

export function hideOnboarding() {
  document.getElementById("onboarding-overlay")?.classList.add("hidden");
  document.removeEventListener("keydown", _onOnboardingKey, true);
}

/** Backdrop-click + Esc both route here. Same effect as the explicit
 *  'Skip — I'll import later' button on step 3: dismiss onboarding
 *  and reveal the empty-state CTA so the user lands on actionable
 *  text, not a blank grid. */
export function _onboardingSkip() {
  return _onboardingFinish(false);
}

/** @param {KeyboardEvent} e */
function _onOnboardingKey(e) {
  if (e.key !== "Escape") return;
  const overlay = document.getElementById("onboarding-overlay");
  if (!overlay || overlay.classList.contains("hidden")) return;
  e.stopPropagation();
  e.stopImmediatePropagation();
  _onboardingSkip();
}

/** Render whichever step is currently active + the dot row. */
export function _renderOnboardingStep() {
  const body = document.getElementById("onboarding-body");
  const dots = document.getElementById("onboarding-dots");
  if (!body) return;

  const steps = [_stepWelcome, _stepLibrary, _stepUseContext, _stepReady];
  steps[_onboardingStep](body);

  // Tag the overlay with the current step so step-specific CSS
  // (e.g. hide the progress indicator on the welcome step) can
  // key off it.
  const overlay = document.getElementById("onboarding-overlay");
  if (overlay) {
    overlay.setAttribute("data-step", String(_onboardingStep));
  }

  if (dots) {
    // Thin progress bar instead of 3 dots — feels less like a
    // generic carousel widget. Filled width = (step+1) / steps.
    const pct = Math.round(((_onboardingStep + 1) / steps.length) * 100);
    dots.innerHTML = `<div class="onb-progress-bar"><div class="onb-progress-fill" style="width: ${pct}%"></div></div>`;
  }
}

/** @param {HTMLElement} body */
export function _stepWelcome(body) {
  body.innerHTML = `
    <div class="onb-hero">
      <img src="/static/img/icon-512.png" alt=""
           class="onb-logo" width="128" height="128">
    </div>
    <h1 class="onb-headline">Best Photo Picker</h1>
    <p class="onb-tagline">
      Find your best shots in seconds — on your own device.
    </p>
    <div class="onb-cta-row">
      <button class="onb-cta" data-action="_onboardingNext">
        Get Started
      </button>
    </div>
    <p class="onb-fine-print">
      Runs entirely on this Mac. No cloud, no account, no tracking.
      <a data-action="_onbToggleFinePrint" id="onb-fineprint-toggle">Details</a>
    </p>
    <p class="onb-fine-print-body" id="onb-fineprint-body" hidden>
      Best Photo Picker contacts <code>api.github.com</code> once at startup to
      check for a newer version (version number only, never your
      library). Disable any time in <strong>Settings → App</strong>.
    </p>
  `;
}

/**
 * Toggle the welcome-step "Details" expansion. Avoids the
 * default <details> chevron which looked carousel-template-ish.
 */
export function _onbToggleFinePrint() {
  const body = document.getElementById("onb-fineprint-body");
  const toggle = document.getElementById("onb-fineprint-toggle");
  if (!body || !toggle) return;
  const isHidden = body.hasAttribute("hidden");
  if (isHidden) {
    body.removeAttribute("hidden");
    toggle.textContent = "Hide";
  } else {
    body.setAttribute("hidden", "");
    toggle.textContent = "Details";
  }
}

/** @param {HTMLElement} body */
export function _stepLibrary(body) {
  const defaultPath = _onboardingLibPath || "~/Pictures/BestPhotoPicker";
  body.innerHTML = `
    <div class="onboarding-icon">
      <svg viewBox="0 0 48 48" fill="none" stroke="var(--accent)" stroke-width="2.5" style="width:48px;height:48px">
        <path d="M6 14v22a4 4 0 004 4h28a4 4 0 004-4V18a4 4 0 00-4-4H24l-4-4H10a4 4 0 00-4 4z"/>
      </svg>
    </div>
    <h2 class="onboarding-title">Your Library Location</h2>
    <p class="onboarding-desc">
      Photos, thumbnails, and database are stored here.
      The default location works great for most users.
    </p>
    <div class="onboarding-field">
      <div class="onboarding-path-display" title="${escapeAttr(defaultPath)}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
             class="onboarding-path-icon">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
        <span class="onboarding-path-text" id="onboarding-lib-path-display"
              dir="rtl">${esc(defaultPath)}</span>
        <input type="hidden" id="onboarding-lib-path" value="${escapeAttr(defaultPath)}">
        <button class="onboarding-path-change" data-action="_onboardingBrowse">Change</button>
      </div>
    </div>
    <div class="onboarding-warning" id="onboarding-cloud-warn" style="display:none">
      <svg viewBox="0 0 16 16" fill="var(--yellow, #f0b429)" style="width:16px;height:16px;flex-shrink:0">
        <path d="M8 1L1 14h14L8 1zm0 4.5v4m0 1.5v1"/>
      </svg>
      <span>This path appears to be in a cloud-synced folder (iCloud, Dropbox, OneDrive).
      SQLite databases may corrupt if synced by multiple devices. Consider using a local folder instead.</span>
    </div>
    <div class="onboarding-actions">
      <button class="btn-secondary onboarding-btn-back" data-action="_onboardingBack">Back</button>
      <button class="btn-primary onboarding-btn" data-action="_onboardingNext">Continue</button>
    </div>
  `;
  _checkCloudPath(defaultPath);
}

/**
 * Show the cloud-sync warning when the path matches a known cloud
 * provider mount point. SQLite + cloud sync = data loss risk.
 *
 * @param {string} path
 */
export function _checkCloudPath(path) {
  const warn = /** @type {HTMLElement | null} */ (
    document.getElementById("onboarding-cloud-warn")
  );
  if (!warn) return;
  const cloudPatterns = [
    "/Library/Mobile Documents/", // iCloud
    "/iCloud Drive/",
    "/CloudStorage/",
    "/Dropbox/",
    "/OneDrive/",
    "/Google Drive/",
    "/Box/",
  ];
  const isCloud = cloudPatterns.some((p) => path.includes(p));
  warn.style.display = isCloud ? "flex" : "none";
}

/** Open the native folder picker; update the input + cloud-warn check on success. */
export async function _onboardingBrowse() {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (data.path) {
      const input = /** @type {HTMLInputElement | null} */ (
        document.getElementById("onboarding-lib-path")
      );
      if (input) input.value = data.path;
      const display = document.getElementById("onboarding-lib-path-display");
      if (display) {
        display.textContent = data.path;
        const wrap = display.closest(".onboarding-path-display");
        if (wrap) wrap.setAttribute("title", data.path);
      }
      _onboardingLibPath = data.path;
      _checkCloudPath(data.path);
    }
  } catch (e) {
    toastError("open the folder picker", e);
  }
}

/** @type {string} */
let _onboardingUseContext = "personal";

/**
 * Use-context step — sets the legal posture for restricted models.
 * Asked BEFORE any model can load so the runtime gate behaves
 * correctly from the first analyze. The value is persisted via
 * /api/v1/model-registry/use-context on Continue.
 *
 * @param {HTMLElement} body
 */
export function _stepUseContext(body) {
  const cards = USE_CONTEXT_OPTIONS
    .map(
      (o) => `
      <label class="onb-context-card${
        o.value === _onboardingUseContext ? " selected" : ""
      }">
        <input type="radio" name="onb-use-context" value="${escapeAttr(o.value)}"
               ${o.value === _onboardingUseContext ? "checked" : ""}>
        <div class="onb-context-card-title">${esc(o.title)}</div>
        <div class="onb-context-card-desc">${esc(o.desc)}</div>
      </label>`,
    )
    .join("");
  body.innerHTML = `
    <div class="onboarding-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
           style="width:44px;height:44px">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      </svg>
    </div>
    <h2 class="onboarding-title">How will you use Best Photo Picker?</h2>
    <p class="onboarding-desc">
      Stored locally. You can change this any time in Settings.
    </p>
    <div class="onb-context-cards" data-onchange="_onbSetUseContext">
      ${cards}
    </div>
    <div class="onboarding-actions">
      <button class="btn-secondary onboarding-btn-back" data-action="_onboardingBack">Back</button>
      <button class="btn-primary onboarding-btn" data-action="_onboardingNext">Continue</button>
    </div>
  `;
}

/**
 * onchange handler for the use-context radio cards. Stores the
 * choice in module-scope so _persistOnboardingUseContext can POST
 * it on Continue.
 *
 * @param {string} value
 */
export function _onbSetUseContext(value) {
  if (!value) return;
  _onboardingUseContext = value;
  // Visual update: flip the .selected class on the cards. The
  // input being checked already shows the radio dot; the wrapper
  // .selected gives the whole card a highlighted border.
  for (const card of document.querySelectorAll(".onb-context-card")) {
    const input = /** @type {HTMLInputElement | null} */ (
      card.querySelector("input[name='onb-use-context']")
    );
    if (input && input.value === value) {
      card.classList.add("selected");
    } else {
      card.classList.remove("selected");
    }
  }
}

/**
 * POST the chosen use-context to the registry endpoint. Best-effort:
 * a network failure shouldn't block the wizard from advancing —
 * users can fix the value from Settings → ML Models if it doesn't
 * land.
 */
async function _persistOnboardingUseContext() {
  try {
    await apiFetch("/api/v1/model-registry/use-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_context: _onboardingUseContext }),
    });
  } catch (e) {
    toastError("save your choice", e);
  }
}

/** @param {HTMLElement} body */
export function _stepReady(body) {
  body.innerHTML = `
    <div class="onboarding-icon">
      <svg viewBox="0 0 48 48" fill="none" stroke="var(--green, #2cb67d)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="width:48px;height:48px">
        <circle cx="24" cy="24" r="20"/>
        <path d="M15 24l6 6 12-12"/>
      </svg>
    </div>
    <h2 class="onboarding-title">You're all set</h2>
    <p class="onboarding-desc">
      Your library is ready. Import a folder of photos to get started,
      or explore the app first.
    </p>
    <div class="onboarding-actions onboarding-actions-stacked">
      <button class="btn-primary onboarding-btn" data-action="_onboardingFinish" data-arg0="true">Import Photos Now</button>
      <button class="btn-secondary onboarding-btn-skip" data-action="_onboardingFinish" data-arg0="false">Skip &mdash; I'll import later</button>
    </div>
  `;
}

export function _onboardingNext() {
  if (_onboardingStep === 1) {
    const input = /** @type {HTMLInputElement | null} */ (
      document.getElementById("onboarding-lib-path")
    );
    if (input) _onboardingLibPath = input.value;
  }
  if (_onboardingStep === 2) {
    void _persistOnboardingUseContext();
  }
  _onboardingStep = Math.min(_onboardingStep + 1, 3);
  _renderOnboardingStep();
}

export function _onboardingBack() {
  _onboardingStep = Math.max(_onboardingStep - 1, 0);
  _renderOnboardingStep();
}

/**
 * Finish the onboarding: persist the chosen library path (if user
 * changed it from the default), close the overlay, optionally open
 * the import modal.
 *
 * @param {boolean} importNow
 */
export async function _onboardingFinish(importNow) {
  /** @type {any} */
  const win = window;
  const input = /** @type {HTMLInputElement | null} */ (
    document.getElementById("onboarding-lib-path")
  );
  const newPath = input ? input.value : _onboardingLibPath;
  if (newPath && newPath !== _onboardingLibPath) {
    try {
      await apiFetch("/api/v1/libraries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: newPath }),
      });
      await apiFetch("/api/v1/libraries/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: newPath }),
      });
    } catch (e) {
      toastError("set the library location", e);
    }
  }

  hideOnboarding();

  if (importNow) {
    win.showImportModal?.();
  } else {
    // Skip-and-explore branch: reveal the welcome empty-state panel
    // (icon + "Import Photos" CTA + the 4-step graphic, defined in
    // index.html) so the user lands on actionable text instead of a
    // bare hidden grid wondering whether onboarding finished or the
    // app broke. Same surface non-first-run startup uses for an
    // empty library.
    win.showEmptyLibrary?.();
  }
}
