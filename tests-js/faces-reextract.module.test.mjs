// @ts-check
import { afterEach, describe, expect, test, vi } from "vitest";

const apiFetch = vi.fn(() => Promise.resolve({}));
const appConfirm = vi.fn();

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch,
  authedSrc: (s) => s,
  authEventSource: () => ({ close() {}, addEventListener() {}, onmessage: null, onerror: null }),
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({ appConfirm }));

const { confirmRetryFaceExtraction } = await import("../bpp/web/static/js/modules/faces.mjs");

afterEach(() => {
  apiFetch.mockClear();
  appConfirm.mockReset();
});

describe("confirmRetryFaceExtraction", () => {
  test("confirmed → POSTs /api/v1/faces/retry", async () => {
    appConfirm.mockResolvedValue(true);
    await confirmRetryFaceExtraction();
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/faces/retry", { method: "POST" });
  });

  test("cancelled → does NOT wipe face data", async () => {
    appConfirm.mockResolvedValue(false);
    await confirmRetryFaceExtraction();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
