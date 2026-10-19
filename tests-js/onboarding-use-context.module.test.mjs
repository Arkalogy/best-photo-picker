// @ts-check
// Choosing the use context in onboarding changes which models the load
// gate allows, so it announces a model-settings change like Settings does.
import { describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn().mockResolvedValue({}),
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

const onboarding = await import("../bpp/web/static/js/modules/onboarding.mjs");
const { MODELS_CHANGED_EVENT } = await import("../bpp/web/static/js/modules/models-changed.mjs");

describe("onboarding use context", () => {
  test("saving it announces the change", async () => {
    const heard = vi.fn();
    document.addEventListener(MODELS_CHANGED_EVENT, heard);
    onboarding._onbSetUseContext("commercial");
    await onboarding._persistOnboardingUseContext();
    document.removeEventListener(MODELS_CHANGED_EVENT, heard);
    expect(heard).toHaveBeenCalledTimes(1);
  });
});
