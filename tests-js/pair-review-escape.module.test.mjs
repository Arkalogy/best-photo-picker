// @ts-check
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ u) => u,
  faceCropUrl: () => "",
}));
vi.mock("../bpp/web/static/js/modules/faces.mjs", () => ({ loadFaceClusters: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({ personDisplayName: () => "P" }));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast: vi.fn(), toastError: vi.fn() }));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { startFacePairReview } from "../bpp/web/static/js/modules/people-pair-review.mjs";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("pair review overlay", () => {
  test("Escape is consumed before global handlers and the panel is a dialog", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      pairs: [
        {
          cluster_a: {
            id: 11,
            name: "A",
            face_count: 2,
            photo_count: 2,
            representative: { thumb_hash: "ha", face_index: 0 },
          },
          cluster_b: {
            id: 12,
            name: "B",
            face_count: 2,
            photo_count: 2,
            representative: { thumb_hash: "hb", face_index: 0 },
          },
          distance: 0.5,
        },
      ],
      threshold: 0.792,
    });
    const later = vi.fn();
    document.addEventListener("keydown", later); // a bubble-phase global handler
    await startFacePairReview();
    const overlay = document.getElementById("face-pair-review-overlay");
    expect(overlay?.classList.contains("visible")).toBe(true);
    const panel = overlay?.querySelector(".pair-review-panel");
    expect(panel?.getAttribute("role")).toBe("dialog");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(overlay?.classList.contains("visible")).toBe(false);
    expect(later).not.toHaveBeenCalled();
    document.removeEventListener("keydown", later);
  });
});
