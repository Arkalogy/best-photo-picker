// @ts-check
/**
 * A model switched off for analysis (its `model_*` toggle is false) shows
 * as "Off" in the picker, with a menu switch to turn it back on; a model
 * that runs offers the opposite switch. Before 0.2.2 the flag was honoured
 * by analysis but invisible here, and no UI could flip it back.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn().mockResolvedValue({});

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const { _resetFaceEmbedderStateForTests, _deriveRowState, ROW_LIFECYCLE } =
  await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");
const { _buildMenuFromState } =
  await import("../bpp/web/static/js/modules/face-embedders-rowstate.mjs");
const { _statusCell } = await import("../bpp/web/static/js/modules/face-embedders-cells.mjs");
const { setModelRunToggle } =
  await import("../bpp/web/static/js/modules/face-embedders-actions.mjs");

const pets = {
  id: "ultralytics_yolo11n",
  display_name: "Pet detection",
  kind: "pet_detector",
  primary_for_kind: true,
  catalog_on_disk: true,
  legacy_label: "Pet detection",
};

/** @param {boolean} enabled */
const petsInstall = (enabled) => ({
  label: "Pet detection",
  toggle_key: "model_pets",
  enabled,
  status: "ready",
  files: [],
  speed_impact: "medium",
  quality_impact: "Finds cats and dogs so you can boost them",
});

beforeEach(() => {
  document.body.innerHTML = `<div id="face-embedder-picker"></div><div id="toast-container"></div>`;
  _resetFaceEmbedderStateForTests();
  apiFetchMock.mockReset().mockResolvedValue({});
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("a model switched off for analysis", () => {
  test("reads Off while keeping its real lifecycle", () => {
    const state = _deriveRowState(pets, petsInstall(false));
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.RUNNING);
    expect(state.switchedOff).toBe(true);
    const html = _statusCell(state);
    expect(html).toContain("Off");
    expect(html).not.toContain("Running");
  });

  test("offers to use it in analysis again", () => {
    const state = _deriveRowState(pets, petsInstall(false));
    const item = _buildMenuFromState(pets, state).find((i) => i.action === "setModelRunToggle");
    expect(item).toBeTruthy();
    expect(item.label).toBe("Use in analysis");
    expect(item.enabled).toBe(true);
    expect(item.args).toEqual(["model_pets", "true", "Pet detection"]);
  });

  test("does not hide a missing runtime behind Off", () => {
    const install = { ...petsInstall(false), status: "no_library", install_hint: "pip install x" };
    const backup = { ...pets, primary_for_kind: false };
    const state = _deriveRowState(backup, install);
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.NEEDS_RUNTIME);
    const html = _statusCell(state);
    expect(html).toContain("Off");
    expect(html).toContain("needs runtime");
    const labels = _buildMenuFromState(backup, state).map((i) => i.label);
    expect(labels).toContain("Install runtime…");
    expect(labels).toContain("Use in analysis");
  });
});

describe("a model that runs", () => {
  test("still reads Running and offers to skip it", () => {
    const state = _deriveRowState(pets, petsInstall(true));
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.RUNNING);
    const item = _buildMenuFromState(pets, state).find((i) => i.action === "setModelRunToggle");
    expect(item.label).toBe("Skip in analysis");
    expect(item.args).toEqual(["model_pets", "false", "Pet detection"]);
    expect(item.sub).toContain("cats and dogs");
  });

  test("a model that is not running is not offered a skip", () => {
    const notDownloaded = { ...pets, catalog_on_disk: false };
    const state = _deriveRowState(notDownloaded, { ...petsInstall(true), status: "missing" });
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.PICKED_NEEDS_DOWNLOAD);
    const items = _buildMenuFromState(notDownloaded, state);
    expect(items.some((i) => i.action === "setModelRunToggle")).toBe(false);
  });

  test("a row without a run switch gets no toggle item", () => {
    const sface = {
      id: "sface_yunet",
      display_name: "SFace",
      kind: "face_embedder",
      method_value: "sface",
      legacy_label: "Face recognition",
    };
    const install = {
      label: "Face recognition",
      status: "ready",
      files: [{ name: "a", exists: true }],
    };
    const items = _buildMenuFromState(sface, _deriveRowState(sface, install));
    expect(items.some((i) => i.action === "setModelRunToggle")).toBe(false);
  });
});

describe("setModelRunToggle", () => {
  test("writes the switch through the toggle endpoint", async () => {
    await setModelRunToggle("model_pets", "true", "Pet detection");
    const call = apiFetchMock.mock.calls.find((c) => c[0] === "/api/v1/models/toggle");
    expect(call).toBeTruthy();
    expect(call[1].method).toBe("POST");
    expect(JSON.parse(call[1].body)).toEqual({ key: "model_pets", enabled: true });
  });

  test("a failure names the action and the reason", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("HTTP 500"));
    await setModelRunToggle("model_pets", "false", "Pet detection");
    const toasts = document.getElementById("toast-container")?.textContent || "";
    expect(toasts).toContain("HTTP 500");
  });
});
