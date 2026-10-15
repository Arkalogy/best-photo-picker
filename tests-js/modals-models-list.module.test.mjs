// @ts-check
// Settings → Models legacy list: plugin rows carry plugin-supplied names,
// and rows bpp can't fetch must offer no Download yet keep Uninstall.
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));
vi.mock("../bpp/web/static/js/modules/modals-face-embedders.mjs", () => ({
  loadFaceEmbedderPicker: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: vi.fn(),
  toastError: vi.fn(),
}));

const { loadModelsList } = await import("../bpp/web/static/js/modules/modals-models-list.mjs");

/** @param {any[]} features */
async function render(features) {
  document.body.innerHTML = '<div id="models-list"></div>';
  apiFetchMock.mockResolvedValue(features);
  await loadModelsList();
  return /** @type {HTMLElement} */ (document.getElementById("models-list"));
}

const NASTY = "Plugin model: Fast \"v2\" <beta> 'x'";

beforeEach(() => apiFetchMock.mockReset());

describe("plugin model rows", () => {
  test("a plugin-supplied name is shown as text and passed intact", async () => {
    const list = await render([
      {
        label: NASTY,
        description: 'Registered by a "plugin" <b>',
        status: "missing",
        size_bytes: 0,
        files: [{ name: 'Fast "v2" <beta>', exists: false, size_bytes: 0 }],
      },
    ]);
    expect(list.querySelector("beta")).toBeNull();
    expect(list.querySelector("b")).toBeNull();
    expect(list.querySelector(".model-name")?.textContent).toContain('Fast "v2" <beta>');
    const btn = /** @type {HTMLElement} */ (
      list.querySelector('[data-action="redownloadFeature"]')
    );
    expect(btn.dataset.arg0).toBe(NASTY);
    expect(JSON.parse(btn.dataset.files || "[]")[0].name).toBe('Fast "v2" <beta>');
  });

  test("a file bpp can't fetch: no Download, Uninstall kept, not in Download all", async () => {
    const list = await render([
      {
        label: "Plugin model: Local",
        description: "Registered by a plugin.",
        status: "missing",
        size_bytes: 0,
        no_download: true,
        download_hint: "managed by its plugin",
        files: [{ name: "Local", exists: false, size_bytes: 0 }],
      },
      {
        label: "Plugin model: OnDisk",
        description: "Registered by a plugin.",
        status: "ready",
        size_bytes: 500,
        no_download: true,
        download_hint: "managed by its plugin",
        files: [{ name: "OnDisk", exists: true, size_bytes: 500 }],
      },
    ]);
    const rows = list.querySelectorAll(".model-row");
    const dl = /** @type {HTMLButtonElement} */ (
      rows[0].querySelector('[data-action="redownloadFeature"]')
    );
    expect(dl.disabled).toBe(true);
    expect(rows[0].textContent).toContain("managed by its plugin");
    expect(rows[0].innerHTML).not.toContain("Install:");
    expect(rows[1].querySelector('[data-action="uninstallFeature"]')).not.toBeNull();
    expect(list.querySelector(".model-download-all")).toBeNull();
  });
});
