// @ts-check
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { closeModal, resolveModal, showModal } from "../bpp/web/static/js/modules/modal.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="modal-overlay" onclick="closeModal(event)">
      <div class="modal">
        <div id="modal-icon"></div>
        <div id="modal-title"></div>
        <div id="modal-body"></div>
        <div id="modal-actions"></div>
      </div>
    </div>
  `;
  /** @type {any} */ (window).resolveModal = resolveModal;
});

afterEach(() => {
  document.body.innerHTML = "";
});

const overlay = () => /** @type {HTMLElement} */ (document.getElementById("modal-overlay"));

describe("showModal — info shape (no confirm)", () => {
  test("populates icon/title/body and shows the overlay", () => {
    showModal("\u{1F44B}", "Welcome", "Get started by importing photos");
    expect(document.getElementById("modal-icon").textContent).toBe("\u{1F44B}");
    expect(document.getElementById("modal-title").textContent).toBe("Welcome");
    expect(document.getElementById("modal-body").textContent).toBe(
      "Get started by importing photos"
    );
    expect(overlay().classList.contains("visible")).toBe(true);
    // Default actions: single OK button
    expect(document.querySelectorAll("#modal-actions .modal-btn")).toHaveLength(1);
  });

  test("OK resolves true via resolveModal", async () => {
    const promise = showModal("i", "Note", "body");
    resolveModal(true);
    await expect(promise).resolves.toBe(true);
    expect(overlay().classList.contains("visible")).toBe(false);
  });
});

describe("showModal — confirm shape", () => {
  test("renders Cancel + custom-labeled confirm button", () => {
    showModal("⚠", "Delete?", "This cannot be undone", { confirm: "Delete" });
    const buttons = document.querySelectorAll("#modal-actions .modal-btn");
    expect(buttons).toHaveLength(2);
    expect(buttons[0].textContent).toBe("Cancel");
    expect(buttons[1].textContent).toBe("Delete");
    expect(buttons[1].classList.contains("modal-btn-primary")).toBe(true);
  });

  test("danger:true styles the confirm button as destructive", () => {
    showModal("⚠", "Wipe DB", "All data will be lost", {
      confirm: "Wipe",
      danger: true,
    });
    const confirmBtn = document.querySelectorAll("#modal-actions .modal-btn")[1];
    expect(confirmBtn.classList.contains("modal-btn-danger")).toBe(true);
    expect(confirmBtn.classList.contains("modal-btn-primary")).toBe(false);
  });

  test("Cancel resolves false; confirm resolves true", async () => {
    const cancelP = showModal("?", "Q", "body", { confirm: "Yes" });
    resolveModal(false);
    await expect(cancelP).resolves.toBe(false);

    const okP = showModal("?", "Q", "body", { confirm: "Yes" });
    resolveModal(true);
    await expect(okP).resolves.toBe(true);
  });
});

describe("closeModal", () => {
  test("treats a backdrop click as cancel (resolves false)", async () => {
    const promise = showModal("?", "Q", "body");
    closeModal(/** @type {any} */ ({ target: overlay() }));
    await expect(promise).resolves.toBe(false);
  });

  test("ignores clicks bubbling up from inner elements", async () => {
    const promise = showModal("?", "Q", "body");
    const inner = document.getElementById("modal-title");
    closeModal(/** @type {any} */ ({ target: inner }));
    // Promise still pending — resolve manually so test doesn't hang
    expect(overlay().classList.contains("visible")).toBe(true);
    resolveModal(false);
    await promise;
  });
});

describe("resilience", () => {
  test("returns false when modal DOM is missing", async () => {
    document.body.innerHTML = "";
    await expect(showModal("?", "missing", "no overlay")).resolves.toBe(false);
  });
});

describe("Esc dismissal", () => {
  test("Escape resolves the modal to false and hides the overlay", async () => {
    const promise = showModal("i", "Note", "body");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await expect(promise).resolves.toBe(false);
    expect(overlay().classList.contains("visible")).toBe(false);
  });

  test("Esc also cancels a confirm-shape modal (parity with appConfirm)", async () => {
    const promise = showModal("?", "Q", "body", { confirm: "Yes" });
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await expect(promise).resolves.toBe(false);
  });

  test("non-Escape keys do not resolve the modal", async () => {
    const promise = showModal("i", "Note", "body");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    // Promise must still be pending — resolve manually so the test exits.
    expect(overlay().classList.contains("visible")).toBe(true);
    resolveModal(false);
    await promise;
  });

  test("Esc handler is removed after dismissal so subsequent modals see a clean handler", async () => {
    // First modal: Esc dismisses cleanly.
    const p1 = showModal("i", "First", "body");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await expect(p1).resolves.toBe(false);

    // Second, totally independent modal: must also work. If the
    // previous handler had leaked it could either fire stale state or
    // accumulate so multiple ones run per Esc.
    const p2 = showModal("i", "Second", "body");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await expect(p2).resolves.toBe(false);
    expect(overlay().classList.contains("visible")).toBe(false);
  });
});
