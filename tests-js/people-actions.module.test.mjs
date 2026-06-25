// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const h = vi.hoisted(() => ({
  apiFetch: vi.fn(async () => ({})),
  toast: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: h.apiFetch,
  authedSrc: (/** @type {string} */ s) => s,
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: h.toast,
  toastError: h.toastError,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn(async () => true),
}));
vi.mock("../bpp/web/static/js/modules/albums.mjs", () => ({
  renderAlbumNav: vi.fn(),
  loadAlbumList: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/faces.mjs", () => ({ loadFaceClusters: vi.fn() }));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({
  isClusterExcluded: () => false,
  // Real signature takes a cluster_id; map 0 → "Leo" for the tests.
  personDisplayName: (/** @type {any} */ cid) => (cid === 0 ? "Leo" : undefined),
  showPeopleView: vi.fn(),
}));

import { state } from "../bpp/web/static/js/modules/state.mjs";
import {
  deleteFacePermanently,
  dismissPerson,
  restoreDismissed,
  restoreFace,
} from "../bpp/web/static/js/modules/people-actions.mjs";

/** Render the real Ignored-grid markup (data-action buttons, not onclick). */
function mountGrid() {
  document.body.innerHTML =
    '<div class="people-filter-pill active">Ignored (2)</div>' +
    '<div id="dismissed-faces-grid">' +
    ["7", "8"]
      .map(
        (id) =>
          `<div class="dismissed-face-cell"><div class="dismissed-face-actions">` +
          `<button class="dismissed-face-restore" data-action="restoreFace" data-arg0="${id}">↩</button>` +
          `<button class="dismissed-face-delete" data-action="deleteFacePermanently" data-arg0="${id}">✕</button>` +
          `</div></div>`
      )
      .join("") +
    "</div>";
  state._dismissedFaces = [{ face_id: 7 }, { face_id: 8 }];
  state._dismissedCount = 2;
}

beforeEach(() => {
  mountGrid();
  h.apiFetch.mockClear();
  h.apiFetch.mockResolvedValue({});
});
afterEach(() => {
  document.body.innerHTML = "";
});

describe("restoreFace — immediate grid removal (regression)", () => {
  test("removes the restored cell from the Ignored grid right away", async () => {
    await restoreFace(7);
    const cells = document.querySelectorAll(".dismissed-face-cell");
    expect(cells).toHaveLength(1); // the bug left it at 2
    expect(document.querySelector('.dismissed-face-restore[data-arg0="7"]')).toBeNull();
    expect(document.querySelector('.dismissed-face-restore[data-arg0="8"]')).toBeTruthy();
  });

  test("decrements the count + updates the pill, and POSTs the restore", async () => {
    await restoreFace(7);
    expect(state._dismissedCount).toBe(1);
    expect(document.querySelector(".people-filter-pill.active").textContent).toBe("Ignored (1)");
    expect(h.apiFetch).toHaveBeenCalledWith(
      "/api/v1/faces/restore",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ face_ids: [7] }) })
    );
  });
});

describe("error-toast policy — a failed mutation names the action", () => {
  beforeEach(() => {
    h.toastError.mockClear();
    state.faceClusters = [{ cluster_id: 0, name: "Leo", filepaths: ["a"] }];
  });

  test("restoreFace HTTP failure → toastError, not an unhandled throw", async () => {
    h.apiFetch.mockRejectedValueOnce(new Error("HTTP 500"));
    await restoreFace(7); // must not reject
    expect(h.toastError).toHaveBeenCalledWith("restore this face", expect.any(Error));
  });

  test("dismissPerson HTTP failure → toastError naming the person", async () => {
    h.apiFetch.mockRejectedValueOnce(new Error("HTTP 500"));
    await dismissPerson(0);
    expect(h.toastError).toHaveBeenCalledWith('dismiss "Leo"', expect.any(Error));
  });

  test("restoreDismissed HTTP failure → toastError", async () => {
    h.apiFetch.mockRejectedValueOnce(new Error("HTTP 500"));
    await restoreDismissed();
    expect(h.toastError).toHaveBeenCalledWith("restore the ignored faces", expect.any(Error));
  });
});

describe("deleteFacePermanently — immediate grid removal (same bug class)", () => {
  test("removes the deleted cell right away + DELETEs the purge", async () => {
    await deleteFacePermanently(8);
    expect(document.querySelectorAll(".dismissed-face-cell")).toHaveLength(1);
    expect(document.querySelector('.dismissed-face-delete[data-arg0="8"]')).toBeNull();
    expect(h.apiFetch).toHaveBeenCalledWith(
      "/api/v1/faces/purge",
      expect.objectContaining({ method: "DELETE" })
    );
  });
});
