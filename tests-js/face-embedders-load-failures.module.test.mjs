// @ts-check
// The Settings → Models picker when one of its supporting calls fails
// (install state, settings, the acceptance list): it says what it couldn't
// load and why, offers a Retry, and marks the rows that needed it
// "Unknown" — never a confident "needs license" or "not downloaded".
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const fe = await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");

const ENTRIES = {
  groups: [
    {
      title: "Models",
      entries: [
        // A restricted model the (older) server sends no load_state for:
        // its license status comes from the acceptance list.
        { id: "restricted_model", display_name: "Restricted", requires_explicit_ack: true },
        // A face embedder: which one runs comes from the settings.
        { id: "insightface_buffalo_s", display_name: "buffalo_s", kind: "face_embedder" },
      ],
    },
  ],
};

/**
 * Answer the picker's calls; *failing* names the URLs that fail.
 * @param {string[]} failing
 */
function serve(failing) {
  apiFetchMock.mockImplementation(async (/** @type {string} */ url) => {
    if (failing.includes(url)) throw new Error("HTTP 500");
    if (url === "/api/v1/model-registry/entries") return ENTRIES;
    if (url === "/api/v1/model-registry/use-context") return { use_context: "personal" };
    if (url === "/api/v1/models") return [];
    if (url === "/api/v1/settings") return { face_embedding_method: "sface" };
    if (url === "/api/v1/model-registry/acceptance/list") {
      return { acceptances: [{ model_id: "restricted_model", event: "accept", current: true }] };
    }
    return {};
  });
}

/** @returns {string} */
const picker = () => document.getElementById("face-embedder-picker")?.textContent || "";

beforeEach(() => {
  document.body.innerHTML = '<div id="face-embedder-picker"></div><div id="toast-container"></div>';
  fe._resetFaceEmbedderStateForTests();
  apiFetchMock.mockReset();
});

describe("a supporting call that fails", () => {
  test("nothing failed: no notice, no 'Unknown' row", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).not.toContain("Couldn't load");
    expect(picker()).not.toContain("Unknown");
  });

  test("the acceptance list: says so, and the accepted model isn't shown as 'needs license'", async () => {
    serve(["/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("Couldn't load your accepted licenses (HTTP 500)");
    // On disk: whether it may load depends on the acceptance.
    const entry = { ...ENTRIES.groups[0].entries[0], ships_with_runtime: true };
    expect(fe._deriveRowState(entry, undefined).lifecycle).toBe(fe.ROW_LIFECYCLE.UNKNOWN);
    // No "accept the license" again (a duplicate accept) in its menu.
    const labels = fe._buildActionMenu(entry, undefined).map((/** @type {any} */ i) => i.label);
    expect(labels.join(" ")).not.toMatch(/accept/i);
  });

  test("the acceptance list: switching to a restricted model isn't done on a guess", async () => {
    // (The settings save has no license gate of its own.)
    serve(["/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    apiFetchMock.mockClear();
    await fe.setActiveFaceEmbedder("arcface", "Restricted", "restricted_model");
    expect(apiFetchMock).not.toHaveBeenCalled();
    // Names the refused action, the reason, and the way back.
    expect(document.getElementById("toast-container")?.textContent).toContain(
      "Couldn't switch to Restricted: the page couldn't load whether its license is accepted (HTTP 500)"
    );
  });

  test("the acceptance list: a model the server lets load reads as accepted (Withdraw stays)", async () => {
    // Current servers say whether a model may load: "ok" for a restricted
    // model means it was accepted — so it may be used, and its license
    // item offers the review-and-withdraw dialog, not accept-again.
    serve(["/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    const entry = { ...ENTRIES.groups[0].entries[0], load_state: "ok", ships_with_runtime: true };
    expect(fe._deriveRowState(entry, undefined).lifecycle).not.toBe(fe.ROW_LIFECYCLE.UNKNOWN);
    const license = fe
      ._buildActionMenu(entry, undefined)
      .find((/** @type {any} */ m) => m.action === "openFaceEmbedderAcceptance");
    expect(license.label).toBe("Model’s license accepted");
    expect(license.args).toEqual(["restricted_model"]);
  });

  test("the acceptance list: switching to a model the server says may load goes ahead", async () => {
    serve(["/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    feState.loadStates.set("restricted_model", "ok");
    apiFetchMock.mockClear();
    serve([]);
    await fe.setActiveFaceEmbedder("arcface", "Restricted", "restricted_model");
    expect(apiFetchMock).toHaveBeenCalledWith("/api/v1/settings", expect.anything());
  });

  test("the settings: the face model in use isn't guessed", async () => {
    serve(["/api/v1/settings"]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("Couldn't load the face model in use");
    const state = fe._deriveRowState(ENTRIES.groups[0].entries[1], undefined);
    expect(state.lifecycle).toBe(fe.ROW_LIFECYCLE.UNKNOWN);
  });

  test("the install state: rows aren't shown as 'not downloaded'", async () => {
    serve(["/api/v1/models"]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("Couldn't load install status");
    const yunet = { id: "opencv_yunet", display_name: "YuNet", primary_for_kind: true };
    expect(fe._deriveRowState(yunet, undefined).lifecycle).toBe(fe.ROW_LIFECYCLE.UNKNOWN);
    // Its menu offers no download or uninstall built on the guess.
    const menu = fe._buildActionMenu(yunet, undefined);
    expect(menu.map((/** @type {any} */ i) => i.action)).toEqual(["loadFaceEmbedderPicker"]);
    expect(picker()).not.toContain("~"); // no "would download" size estimate either
  });

  test("the install state: a catalog model with no install record keeps Uninstall", async () => {
    serve(["/api/v1/models"]);
    await fe.loadFaceEmbedderPicker();
    const buffalo = {
      id: "insightface_buffalo_s",
      display_name: "buffalo_s",
      is_catalog_entry: true,
      catalog_on_disk: true,
    };
    expect(fe._deriveRowState(buffalo, undefined).lifecycle).not.toBe(fe.ROW_LIFECYCLE.UNKNOWN);
    const actions = fe._buildActionMenu(buffalo, undefined).map((/** @type {any} */ m) => m.action);
    expect(actions).toContain("uninstallFaceEmbedderEntry");
  });

  test("the install state: a catalog model that has one reads Unknown, like the rest", async () => {
    // (Its install record says "needs runtime" / "incomplete".)
    serve(["/api/v1/models"]);
    await fe.loadFaceEmbedderPicker();
    const nudenet = {
      id: "nudenet_320n",
      display_name: "NudeNet",
      is_catalog_entry: true,
      catalog_on_disk: true,
    };
    expect(fe._deriveRowState(nudenet, undefined).lifecycle).toBe(fe.ROW_LIFECYCLE.UNKNOWN);
  });

  test("two failures are listed in a fixed order", async () => {
    serve(["/api/v1/models", "/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toMatch(/install status \(HTTP 500\), your accepted licenses/);
  });

  test("Retry loads it again and clears the notice once it works", async () => {
    const { lookupAction } = await import("../bpp/web/static/js/modules/action-registry.mjs");
    serve(["/api/v1/models"]);
    await fe.loadFaceEmbedderPicker();
    const retry = document.querySelector(
      '#face-embedder-picker [data-action="loadFaceEmbedderPicker"]'
    );
    expect(retry?.textContent).toBe("Retry");
    // The button's action is wired to the picker's loader.
    expect(lookupAction(retry?.getAttribute("data-action") || "")).toBe(fe.loadFaceEmbedderPicker);
    serve([]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).not.toContain("Couldn't load");
  });

  test("a reload mid-download keeps that row locked and showing its progress", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    fe._setRowStatusBusy("opencv_yunet", "Downloading… 40%");
    await fe.loadFaceEmbedderPicker(); // another row's change, or Retry
    expect(feState.busyRowIds.has("opencv_yunet")).toBe(true);
    expect(feState.busyText.get("opencv_yunet")).toBe("Downloading… 40%");
    fe._clearRowBusy("opencv_yunet"); // the download ended
    expect(feState.busyRowIds.size).toBe(0);
  });

  test("a slow failed load finishing after a newer good one doesn't win", async () => {
    /** @type {(e: Error) => void} */
    let failSlowly = () => {};
    apiFetchMock.mockImplementation(async (/** @type {string} */ url) => {
      if (url === "/api/v1/models") return new Promise((_, reject) => (failSlowly = reject));
      return url === "/api/v1/model-registry/entries" ? ENTRIES : {};
    });
    const slow = fe.loadFaceEmbedderPicker();
    serve([]);
    await fe.loadFaceEmbedderPicker(); // the newer one, fine
    failSlowly(new Error("HTTP 500"));
    await slow;
    expect(picker()).not.toContain("Couldn't load");
  });

  test("the server's own decisions show even on an Unknown row (withdrawn is withdrawn)", async () => {
    const { _statusFor } = await import("../bpp/web/static/js/modules/face-embedders-cells.mjs");
    const status = _statusFor({ lifecycle: fe.ROW_LIFECYCLE.UNKNOWN, unavailable: true });
    expect(status.main).toBe("Not available");
    expect(_statusFor({ lifecycle: fe.ROW_LIFECYCLE.UNKNOWN }).main).toBe("Unknown");
  });

  test("the acceptance list: a model the server says needs a license can still be accepted", async () => {
    serve(["/api/v1/model-registry/acceptance/list"]);
    await fe.loadFaceEmbedderPicker();
    const entry = { ...ENTRIES.groups[0].entries[0], load_state: "needs_license" };
    const license = fe
      ._buildActionMenu(entry, undefined)
      .find((/** @type {any} */ m) => m.action === "openFaceEmbedderAcceptance");
    expect(license.args).toEqual(["restricted_model"]); // the accept dialog, not view-only
  });

  test("a withdrawn model can't be switched to, restricted or not", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    feState.loadStates.set("plain_model", "blocked");
    apiFetchMock.mockClear();
    await fe.setActiveFaceEmbedder("plain", "Plain", "plain_model");
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  test("an operation that fails still redraws its row's real status", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    apiFetchMock.mockImplementation(async (/** @type {string} */ url) => {
      if (url === "/api/v1/face-embedders/ensure-weights") throw new Error("HTTP 500");
      return url === "/api/v1/model-registry/entries" ? ENTRIES : {};
    });
    await fe.ensureCatalogWeights("restricted_model", "Restricted");
    await vi.waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/api/v1/model-registry/entries")
    );
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    expect(feState.busyRowIds.has("restricted_model")).toBe(false);
  });

  test("the settings: switching models doesn't overwrite a setting the page never read", async () => {
    serve(["/api/v1/settings"]);
    await fe.loadFaceEmbedderPicker();
    apiFetchMock.mockClear();
    await fe.setActiveFaceEmbedder("buffalo_s", "buffalo_s", "insightface_buffalo_s");
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  test("a withdrawn model's refusal says so, not 'accept the license'", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    feState.loadStates.set("plain_model", "blocked");
    await fe.setActiveFaceEmbedder("plain", "Plain", "plain_model");
    // The refused action, the reason, the way on.
    expect(document.getElementById("toast-container")?.textContent).toContain(
      "Couldn't switch to Plain: it has been withdrawn — pick another model."
    );
  });

  test("a reload that fails while a download runs says the download goes on", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    fe._setRowStatusBusy("opencv_yunet", "Downloading… 40%");
    serve(["/api/v1/model-registry/entries"]);
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("A model download or removal is still running (below).");
    expect(picker()).toContain("Retry");
    fe._clearRowBusy("opencv_yunet");
  });

  test("a finished operation reloads the list once, not twice", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    document.body.innerHTML += '<div id="settings-overlay" class="visible"></div>';
    apiFetchMock.mockImplementation(async (/** @type {string} */ url) => {
      if (url === "/api/v1/face-embedders/ensure-weights") return { job: "j" };
      if (url.startsWith("/api/v1/models/download-jobs/")) return { status: "done", result: {} };
      return url === "/api/v1/model-registry/entries" ? ENTRIES : {};
    });
    apiFetchMock.mockClear();
    await fe.ensureCatalogWeights("restricted_model", "Restricted");
    await new Promise((r) => setTimeout(r, 0));
    const loads = apiFetchMock.mock.calls.filter(
      (/** @type {any[]} */ c) => c[0] === "/api/v1/model-registry/entries"
    );
    expect(loads.length).toBeLessThanOrEqual(1);
  });

  test("the settings: the refusal names the switch, the reason and the way back", async () => {
    serve(["/api/v1/settings"]);
    await fe.loadFaceEmbedderPicker();
    await fe.setActiveFaceEmbedder("buffalo_s", "buffalo_s", "insightface_buffalo_s");
    expect(document.getElementById("toast-container")?.textContent).toContain(
      "Couldn't switch to buffalo_s: the page couldn't load which face model is in use (HTTP 500) — load the list again"
    );
  });

  test("'restricted' is decided the same way for the row, the dialog and the switch", async () => {
    const { _collectRequiresAckIds } =
      await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    const ids = _collectRequiresAckIds([
      {
        entries: [
          { id: "a", requires_explicit_ack: 1 },
          { id: "b", requires_explicit_ack: true },
        ],
      },
    ]);
    expect([...ids]).toEqual(["b"]); // the row's own rule: === true
  });

  test("the notice is a status, not an alert re-read on every reload", async () => {
    serve(["/api/v1/models"]);
    await fe.loadFaceEmbedderPicker();
    const notice = document.querySelector(".fe-load-failure-notice");
    expect(notice?.getAttribute("role")).toBe("status");
  });

  test("a menu item is held back by what it needs, whatever its action is called", async () => {
    const { _withoutGuesses } =
      await import("../bpp/web/static/js/modules/face-embedders-guesses.mjs");
    const known = { installUnknown: false, licenseUnknown: false, methodUnknown: false };
    const items = [
      { label: "New thing", action: "someNewAction", needs: ["install"], enabled: true },
    ];
    expect(_withoutGuesses(items, known)).toEqual(items);
    const kept = _withoutGuesses(items, { ...known, installUnknown: true });
    expect(kept.map((/** @type {any} */ i) => i.action)).toEqual(["loadFaceEmbedderPicker"]);
  });
});

describe("the license dialog when the acceptance list didn't load", () => {
  const acceptance = () => import("../bpp/web/static/js/modules/face-embedders-acceptance.mjs");
  const DIALOG =
    '<div id="fe-acceptance-overlay"><div id="fe-acceptance-body"></div>' +
    '<button id="fe-accept-btn"></button>' +
    '<button data-action="closeFaceEmbedderAcceptance"></button></div>';
  /** @returns {string} */
  const dialog = () => document.getElementById("fe-acceptance-body")?.textContent || "";
  const accept = () => /** @type {HTMLButtonElement} */ (document.getElementById("fe-accept-btn"));

  /**
   * @param {string | undefined} loadState
   * @param {string} [mode]  open in this mode instead of the menu item's
   */
  async function openWith(loadState, mode) {
    document.body.innerHTML += DIALOG;
    serve(["/api/v1/model-registry/acceptance/list"]);
    const base = apiFetchMock.getMockImplementation();
    apiFetchMock.mockImplementation(async (/** @type {string} */ url, /** @type {any} */ o) =>
      url.startsWith("/api/v1/model-registry/acceptance/draft")
        ? {
            model_id: "restricted_model",
            model_display_name: "Restricted",
            required_checkboxes: [],
          }
        : base?.(url, o)
    );
    await fe.loadFaceEmbedderPicker();
    const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
    if (loadState) feState.loadStates.set("restricted_model", loadState);
    const entry = { ...ENTRIES.groups[0].entries[0], load_state: loadState };
    const item = fe
      ._buildActionMenu(entry, undefined)
      .find((/** @type {any} */ m) => m.action === "openFaceEmbedderAcceptance");
    const [id, asked] = mode !== undefined ? ["restricted_model", mode || undefined] : item.args;
    await (await acceptance()).openFaceEmbedderAcceptance(id, asked);
    return item;
  }

  test("the server lets it load: the review dialog (Withdraw), not a fresh accept", async () => {
    await openWith("ok");
    expect(dialog()).toContain("You've accepted these terms");
    expect(accept().textContent).toBe("Withdraw acceptance");
  });

  test("the server wants commercial-use rights: the rights dialog", async () => {
    await openWith("needs_commercial_rights");
    expect(dialog()).toContain("confirm below that you hold separate rights");
  });

  test("no server decision: a read-only view that says it couldn't check — not 'withdrawn'", async () => {
    const item = await openWith(undefined);
    expect(item.args).toEqual(["restricted_model"]); // the dialog decides it's unknown
    expect(dialog()).toContain("Couldn't check whether you've accepted these terms");
    expect(dialog()).not.toMatch(/withdrawn|blocked/i);
    expect(accept().hidden).toBe(true); // nothing to accept or withdraw on a guess
  });

  test("accepting it here makes it known (the reload may fail again)", async () => {
    await openWith(undefined, "accept");
    await (await acceptance()).confirmFaceEmbedderAcceptance();
    const entry = { ...ENTRIES.groups[0].entries[0], ships_with_runtime: true };
    expect(fe._deriveRowState(entry, undefined).lifecycle).not.toBe(fe.ROW_LIFECYCLE.UNKNOWN);
    apiFetchMock.mockClear();
    await fe.setActiveFaceEmbedder("arcface", "Restricted", "restricted_model");
    expect(apiFetchMock).toHaveBeenCalledWith("/api/v1/settings", expect.anything());
  });

  test("accepted here, then the reload fails to fetch the list again: still accepted", async () => {
    await openWith(undefined, "accept");
    await (await acceptance()).confirmFaceEmbedderAcceptance();
    await fe.loadFaceEmbedderPicker(); // the list fails again
    const entry = { ...ENTRIES.groups[0].entries[0], ships_with_runtime: true };
    const state = fe._deriveRowState(entry, undefined);
    expect(state.accepted).toBe(true);
    expect(state.licenseBlocked).toBe(false);
  });

  test("once the list loads, it's the truth again (no stale decision from this page)", async () => {
    await openWith(undefined, "accept");
    await (await acceptance()).revokeFaceEmbedderAcceptance("restricted_model");
    serve([]); // the list loads: it says accepted
    await fe.loadFaceEmbedderPicker();
    const entry = { ...ENTRIES.groups[0].entries[0], ships_with_runtime: true };
    expect(fe._deriveRowState(entry, undefined).accepted).toBe(true);
  });

  test("opened any other way while it's unknown: still the read-only view", async () => {
    await openWith(undefined, "");
    expect(dialog()).toContain("Couldn't check whether you've accepted these terms");
    expect(accept().hidden).toBe(true);
  });

  test("a withdrawn model's view doesn't silently assume it was never accepted", async () => {
    await openWith("blocked", "view");
    expect(dialog()).toContain("Couldn't check whether you'd accepted it");
  });

  test("the reload after accepting keeps the rows on screen (no jump to the top)", async () => {
    await openWith(undefined, "accept");
    await (await acceptance()).confirmFaceEmbedderAcceptance();
    const container = /** @type {HTMLElement} */ (document.getElementById("face-embedder-picker"));
    const rowsBefore = container.querySelectorAll("tr[data-entry-id]").length;
    expect(rowsBefore).toBeGreaterThan(0);
    const reload = fe.loadFaceEmbedderPicker(); // what "models changed" triggers
    expect(container.querySelectorAll("tr[data-entry-id]").length).toBe(rowsBefore); // kept
    expect(container.getAttribute("aria-busy")).toBe("true");
    await reload;
    expect(container.getAttribute("aria-busy")).toBe(null);
  });

  test("withdrawing it here makes it known: it needs a license", async () => {
    await openWith(undefined, "accept");
    await (await acceptance()).revokeFaceEmbedderAcceptance("restricted_model");
    const entry = { ...ENTRIES.groups[0].entries[0], ships_with_runtime: true };
    expect(fe._deriveRowState(entry, undefined).lifecycle).toBe(fe.ROW_LIFECYCLE.NEEDS_LICENSE);
  });
});

describe("a running download while the list reloads", () => {
  test("its progress stays on screen during Loading… and after a failed reload", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    fe._setRowStatusBusy("insightface_buffalo_s", "Downloading… 40%");
    serve(["/api/v1/model-registry/entries"]);
    const reload = fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("Downloading… 40%"); // while loading (its row stays)
    await reload;
    expect(picker()).toContain("Downloading… 40%"); // after it failed (its own line)
    fe._setRowStatusBusy("insightface_buffalo_s", "Downloading… 55%"); // later progress lands too
    expect(picker()).toContain("Downloading… 55%");
    fe._clearRowBusy("insightface_buffalo_s");
  });

  test("a busy row the reloaded list doesn't draw still shows its progress", async () => {
    serve([]);
    await fe.loadFaceEmbedderPicker();
    fe._setRowStatusBusy("opencv_yunet", "Downloading… 40%"); // not in ENTRIES
    await fe.loadFaceEmbedderPicker(); // succeeds, without that row
    expect(picker()).toContain("Downloading… 40%");
    fe._clearRowBusy("opencv_yunet");
  });

  test("the list failing entirely: names it once, without a doubled full stop", async () => {
    apiFetchMock.mockImplementation(async () => {
      throw new Error("Couldn't read the use context.");
    });
    await fe.loadFaceEmbedderPicker();
    expect(picker()).toContain("Couldn't load the model list: Couldn't read the use context.");
    expect(picker()).not.toContain("..");
  });
});
