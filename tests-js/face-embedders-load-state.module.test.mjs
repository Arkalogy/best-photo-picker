// @ts-check
// Picker rows follow the server's load-gate decision (`load_state`), so a
// model the gate refuses — commercial use without the rights assertion,
// or withdrawn / legally blocked — isn't shown as running or accepted.
import { beforeEach, describe, expect, test } from "vitest";

import {
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
} from "../bpp/web/static/js/modules/face-embedders-state.mjs";
import {
  _buildMenuFromState,
  _deriveRowState,
} from "../bpp/web/static/js/modules/face-embedders-rowstate.mjs";
import { _statusFor } from "../bpp/web/static/js/modules/face-embedders-cells.mjs";

const nudenet = {
  id: "nudenet_320n",
  display_name: "NudeNet 320n",
  kind: "nudity_classifier",
  requires_explicit_ack: true,
  default_for_kind: true,
  is_catalog_entry: true,
  catalog_on_disk: true,
};

beforeEach(() => _resetFaceEmbedderStateForTests());

/** @param {string} loadState */
function row(loadState, accepted = true) {
  _seedFaceEmbedderStateForTests({ acceptedIds: accepted ? ["nudenet_320n"] : [] });
  const entry = { ...nudenet, load_state: loadState };
  const state = _deriveRowState(entry, undefined);
  return { state, status: _statusFor(state), menu: _buildMenuFromState(entry, state) };
}

describe("load_state from the server wins", () => {
  test("commercial use without the rights assertion: blocked, says why, offers the dialog", () => {
    const { state, status, menu } = row("needs_commercial_rights");
    expect(state.licenseBlocked).toBe(true);
    expect(status.main).toBe("Needs commercial-use rights");
    const license = menu.find((/** @type {any} */ i) => i.action === "openFaceEmbedderAcceptance");
    expect(license.label).toMatch(/commercial-use rights/);
    expect(license.enabled).toBe(true);
    expect(license.done).toBe(false); // no ✓ until the rights are confirmed
    const download = menu.find((/** @type {any} */ i) => i.action === "ensureCatalogWeights");
    expect(download.enabled).toBe(false);
    expect(download.sub).toMatch(/commercial-use rights first/);
  });

  test("withdrawn / legally blocked: not available, no download", () => {
    const { state, status, menu } = row("blocked");
    expect(state.licenseBlocked).toBe(true);
    expect(status.main).toBe("Not available");
    const download = menu.find((/** @type {any} */ i) => i.action === "ensureCatalogWeights");
    expect(download.enabled).toBe(false);
    expect(download.sub).toMatch(/no longer available/i);
  });

  test("withdrawn and never accepted: the license can only be viewed", () => {
    const { menu } = row("blocked", false);
    const license = menu.find((/** @type {any} */ i) => i.action === "openFaceEmbedderAcceptance");
    expect(license.label).toBe("View the model’s license");
    expect(license.args).toEqual(["nudenet_320n", "view"]);
  });

  test("ok: runs even if the acceptance list wasn't loaded", () => {
    const { state } = row("ok", false);
    expect(state.licenseBlocked).toBe(false);
    expect(state.lifecycle).toBe("running");
  });

  test("needs_reaccept from the server shows the re-accept state", () => {
    const { status } = row("needs_reaccept");
    expect(status.main).toBe("Re-accept needed");
  });

  test("older server without load_state keeps the acceptance-list logic", () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: [] });
    const state = _deriveRowState({ ...nudenet }, undefined);
    expect(state.licenseBlocked).toBe(true);
  });
});
