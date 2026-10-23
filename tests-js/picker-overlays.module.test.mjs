// @ts-check
/**
 * The people and pet pickers are dialogs: announced as such, reachable
 * and operable from the keyboard, closed by Escape without the key going
 * on to the lightbox, and they hand focus back where it came from. The
 * pet merge picker also used to leave its Escape listener behind after
 * every close.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn(async () => false),
  appPrompt: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(async (/** @type {string} */ url) => {
    if (url.includes("/faces/cluster/")) {
      return {
        faces: [
          { thumb_hash: "h1", face_index: 0, filepath: "/a.jpg" },
          { thumb_hash: "h2", face_index: 0, filepath: "/b.jpg" },
        ],
        total: 2,
      };
    }
    if (url.includes("/pets/cluster/")) {
      return {
        detections: [{ thumb_hash: "h1", detection_index: 0, filepath: "/a.jpg" }],
        total: 1,
      };
    }
    return {};
  }),
  authedSrc: (/** @type {string} */ s) => s,
  petCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/pets/crop/${h}/${i}`,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/crop/${h}/${i}`,
}));

import { appConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";
import {
  showAvatarPicker,
  showMergePicker,
  showSplitPicker,
} from "../bpp/web/static/js/modules/people-pickers.mjs";
import {
  showIdentifyPicker,
  showPetMergePicker,
} from "../bpp/web/static/js/modules/pets-pickers.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

/** @type {import("vitest").Mock<(e: KeyboardEvent) => void>} */
let later;

beforeEach(() => {
  document.body.innerHTML = `<button id="opener">open</button><div id="toast-container"></div>`;
  /** @type {any} */ (state).faceClusters = [
    {
      cluster_id: 1,
      name: "Kai",
      photo_count: 3,
      representative: { thumb_hash: "h1", face_index: 0 },
    },
    {
      cluster_id: 2,
      name: "Mia",
      photo_count: 2,
      representative: { thumb_hash: "h2", face_index: 0 },
    },
  ];
  /** @type {any} */ (state).albumList = [];
  /** @type {any} */ (window).petClusters = [
    {
      cluster_id: 1,
      pet_class: "dog",
      photo_count: 3,
      representative: { thumb_hash: "p1", detection_index: 0 },
    },
    {
      cluster_id: 2,
      pet_class: "dog",
      photo_count: 2,
      representative: { thumb_hash: "p2", detection_index: 0 },
    },
  ];
  document.getElementById("opener")?.focus();
  later = vi.fn((/** @type {KeyboardEvent} */ _e) => {});
  document.addEventListener("keydown", later);
});

afterEach(() => {
  document.removeEventListener("keydown", later);
  document.body.innerHTML = "";
});

/** @param {string} key @param {Element} [target] */
function press(key, target) {
  (target || document.body).dispatchEvent(
    new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })
  );
}

const flush = () => new Promise((r) => setTimeout(r, 60));

describe("people pickers", () => {
  test("the merge picker is a dialog, takes focus, and Escape closes only it", () => {
    showMergePicker(1);
    const overlay = /** @type {HTMLElement} */ (document.getElementById("merge-picker-overlay"));
    expect(overlay.getAttribute("role")).toBe("dialog");
    expect(overlay.getAttribute("aria-modal")).toBe("true");
    expect(overlay.contains(document.activeElement)).toBe(true);
    press("Escape");
    expect(document.getElementById("merge-picker-overlay")).toBeNull();
    expect(later).not.toHaveBeenCalled();
    expect(document.activeElement?.id).toBe("opener");
  });

  test("a merge target can be chosen from the keyboard", () => {
    showMergePicker(1);
    const item = /** @type {HTMLElement} */ (document.querySelector(".merge-picker-item"));
    expect(item.tabIndex).toBe(0);
    item.focus();
    press("Enter", item);
    expect(appConfirm).toHaveBeenCalled();
  });

  test("the split and avatar pickers are dialogs with keyboard-pickable faces", async () => {
    await showSplitPicker(1);
    await flush();
    const split = /** @type {HTMLElement} */ (document.getElementById("split-picker-overlay"));
    expect(split.getAttribute("role")).toBe("dialog");
    expect(/** @type {HTMLElement} */ (split.querySelector(".face-select-cell")).tabIndex).toBe(0);
    press("Escape");
    expect(document.getElementById("split-picker-overlay")).toBeNull();

    await showAvatarPicker(1);
    await flush();
    const avatar = /** @type {HTMLElement} */ (document.getElementById("avatar-picker-overlay"));
    expect(avatar.getAttribute("role")).toBe("dialog");
    const cell = /** @type {HTMLElement} */ (avatar.querySelector("[tabindex='0']"));
    expect(cell).not.toBeNull();
    press("Escape");
    expect(document.getElementById("avatar-picker-overlay")).toBeNull();
  });
});

describe("pet pickers", () => {
  test("the merge picker closes on Escape, hands focus back, and leaves no listener", () => {
    showPetMergePicker(1);
    const overlay = /** @type {HTMLElement} */ (
      document.getElementById("pet-merge-picker-overlay")
    );
    expect(overlay.getAttribute("role")).toBe("dialog");
    expect(overlay.contains(document.activeElement)).toBe(true);
    press("Escape");
    expect(document.getElementById("pet-merge-picker-overlay")).toBeNull();
    expect(later).not.toHaveBeenCalled();
    expect(document.activeElement?.id).toBe("opener");
    press("Escape"); // nothing open: the key goes on to whoever owns it
    expect(later).toHaveBeenCalledTimes(1);
  });

  test("the identify picker is a dialog with keyboard-pickable crops", async () => {
    await showIdentifyPicker(1);
    await flush();
    const overlay = /** @type {HTMLElement} */ (document.getElementById("identify-picker-overlay"));
    expect(overlay.getAttribute("role")).toBe("dialog");
    expect(overlay.querySelector("[tabindex='0']")).not.toBeNull();
    press("Escape");
    expect(document.getElementById("identify-picker-overlay")).toBeNull();
    expect(document.activeElement?.id).toBe("opener");
  });
});
