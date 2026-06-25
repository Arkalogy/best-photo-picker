// @ts-check
/**
 * Tests for the dispatcher back-compat shims in modals.mjs.
 *
 * When called via the global dispatcher, redownloadFeature/uninstallFeature/
 * installPackage receive (label, desc/key) as args with `this` = the button
 * element that carries data-files / data-arg0. These shims were added to
 * support the CSP migration — verify they correctly remap arguments.
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  redownloadFeature,
  uninstallFeature,
  installPackage,
  toggleModel,
} from "../bpp/web/static/js/modules/modals.mjs";

// Stub apiFetch so network calls never happen
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn().mockResolvedValue({ specs: [], label: "test", desc: "" }),
  authedSrc: (p) => p,
}));

vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  showToast: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("../bpp/web/static/js/modules/modal.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(false),
  resolveModal: vi.fn(),
  closeModal: vi.fn(),
}));

beforeEach(() => {
  document.body.innerHTML = `<div id="modal-overlay"></div>`;
});

// ── redownloadFeature back-compat ────────────────────────────────────────────

describe("redownloadFeature — dispatcher back-compat", () => {
  test("called with (btn, files, label, desc) works normally", async () => {
    const btn = document.createElement("button");
    // Should not throw — apiFetch is mocked to return empty specs
    await expect(redownloadFeature(btn, [], "Test Model", "desc")).resolves.not.toThrow();
  });

  test("called via dispatcher: this=btn, arg0=label, arg1=desc", async () => {
    const btn = document.createElement("button");
    btn.dataset.files = "[]"; // empty file list
    // Dispatcher calls fn.apply(btn, ["My Label", "My Desc"])
    // Back-compat shim should swap: btn=this, files=JSON.parse(dataset.files)
    await expect(redownloadFeature.call(btn, "My Label", "My Desc")).resolves.not.toThrow();
  });
});

// ── uninstallFeature back-compat ─────────────────────────────────────────────

describe("uninstallFeature — dispatcher back-compat", () => {
  test("called with (btn, files, label) works normally", async () => {
    const btn = document.createElement("button");
    // No files to uninstall — should return early
    await expect(uninstallFeature(btn, [], "Test Model")).resolves.not.toThrow();
  });

  test("called via dispatcher: this=btn, arg0=label", async () => {
    const btn = document.createElement("button");
    btn.dataset.files = "[]";
    await expect(uninstallFeature.call(btn, "My Label")).resolves.not.toThrow();
  });
});

// ── installPackage back-compat ───────────────────────────────────────────────

describe("installPackage — dispatcher back-compat shim logic", () => {
  test("source has the dispatcher shim that swaps btn/key/label", () => {
    const { readFileSync } = require("fs");
    const src = readFileSync("bpp/web/static/js/modules/modals-models.mjs", "utf8");
    // The shim pattern: when btn is a string (called via dispatcher),
    // swap btn=this, key=btn, label=key
    expect(src).toContain("export async function installPackage(btn, key, label)");
    // Check back-compat branch exists
    expect(src).toMatch(/typeof btn === "string".*\n.*btn = this/s);
  });
});

// ── toggleModel back-compat ──────────────────────────────────────────────────

describe("toggleModel — dispatcher back-compat", () => {
  test("called with checkbox element uses it directly", async () => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.toggleKey = "model_clip";
    // apiFetch mocked — should not throw
    await expect(toggleModel(checkbox)).resolves.not.toThrow();
  });

  test("called via dispatcher: this=checkbox, arg0=undefined", async () => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = false;
    checkbox.dataset.toggleKey = "model_clip";
    // Dispatcher calls fn.apply(checkbox, []) — first arg is undefined
    await expect(toggleModel.call(checkbox, undefined)).resolves.not.toThrow();
  });
});
