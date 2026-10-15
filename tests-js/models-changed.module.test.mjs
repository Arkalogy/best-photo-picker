// @ts-check
// After an accept / revoke / download / uninstall, the content-filter
// hint refreshes from /api/v1/status instead of waiting for a reload.
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));
vi.mock("../bpp/web/static/js/modules/analysis.mjs", () => ({ scheduleRecompute: vi.fn() }));

const { notifyModelsChanged } = await import("../bpp/web/static/js/modules/models-changed.mjs");
await import("../bpp/web/static/js/modules/ui-helpers.mjs");

beforeEach(() => {
  apiFetchMock.mockReset();
  document.body.innerHTML = '<span id="sensitive-filter-tip" data-tip="old"></span>';
  /** @type {any} */ (window).nudenetActive = false;
});

describe("model settings changed", () => {
  test("refreshes the content-filter hint from the server", async () => {
    apiFetchMock.mockResolvedValue({ active: true });
    notifyModelsChanged();
    const tip = /** @type {HTMLElement} */ (document.getElementById("sensitive-filter-tip"));
    await vi.waitFor(() => expect(tip.dataset.tip).toMatch(/is on\./));
    expect(apiFetchMock).toHaveBeenCalledWith("/api/v1/models/content-filter");
    expect(/** @type {any} */ (window).nudenetActive).toBe(true);
  });

  test("an older answer arriving last doesn't overwrite a newer one", async () => {
    /** @type {Array<(v: any) => void>} */
    const pending = [];
    apiFetchMock.mockImplementation(() => new Promise((res) => pending.push(res)));
    notifyModelsChanged(); // older: will say "off"
    notifyModelsChanged(); // newer: will say "on"
    pending[1]({ active: true });
    await new Promise((r) => setTimeout(r, 0));
    pending[0]({ active: false });
    await new Promise((r) => setTimeout(r, 0));
    const tip = /** @type {HTMLElement} */ (document.getElementById("sensitive-filter-tip"));
    expect(tip.dataset.tip).toMatch(/is on\./);
  });

  test("a failed status fetch leaves the hint alone", async () => {
    apiFetchMock.mockRejectedValue(new Error("offline"));
    notifyModelsChanged();
    await new Promise((r) => setTimeout(r, 0));
    const tip = /** @type {HTMLElement} */ (document.getElementById("sensitive-filter-tip"));
    expect(tip.dataset.tip).toBe("old");
  });
});
