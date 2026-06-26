// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { appConfirm, appPrompt, resolveConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="confirm-overlay">
      <div class="confirm-dialog"></div>
    </div>
  `;
  // The button onclick="" attributes call resolveConfirm globally
  // since they live in template-rendered HTML strings — bridge it
  // onto window the same way index.html does.
  /** @type {any} */ (window).resolveConfirm = resolveConfirm;
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const overlay = () => /** @type {HTMLElement} */ (document.getElementById("confirm-overlay"));
const dialog = () => /** @type {HTMLElement} */ (document.querySelector(".confirm-dialog"));

// jsdom doesn't reliably evaluate inline `onclick="resolveConfirm(false)"`
// attributes set via innerHTML — the compiled handler closes over the
// inline attribute's scope, not window's. Tests below resolve via the
// public `resolveConfirm()` API directly, which is exactly what the
// real onclick=""s end up calling at runtime in the browser.

describe("appConfirm", () => {
  test("renders message into the dialog and shows the overlay", async () => {
    const promise = appConfirm("Delete this?");
    expect(dialog().textContent).toContain("Delete this?");
    expect(overlay().classList.contains("visible")).toBe(true);

    resolveConfirm(true);
    await expect(promise).resolves.toBe(true);
    expect(overlay().classList.contains("visible")).toBe(false);
  });

  test("Cancel resolves false", async () => {
    const promise = appConfirm("Sure?");
    resolveConfirm(false);
    await expect(promise).resolves.toBe(false);
  });

  test("Esc resolves false and cleans up the keydown listener", async () => {
    const promise = appConfirm("Sure?");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await expect(promise).resolves.toBe(false);
    expect(overlay().classList.contains("visible")).toBe(false);
  });

  test("subtitle string renders below the message", () => {
    appConfirm("Delete photo", "This cannot be undone");
    expect(dialog().textContent).toContain("Delete photo");
    expect(dialog().textContent).toContain("This cannot be undone");
    expect(dialog().querySelector(".confirm-sub")).toBeTruthy();
    resolveConfirm(false);
  });

  test("two-arg shape — opts in subtitle slot", async () => {
    const promise = appConfirm("Confirm?", { okLabel: "Yes please" });
    const ok = /** @type {HTMLButtonElement} */ (document.getElementById("confirm-ok"));
    expect(ok.textContent).toBe("Yes please");
    resolveConfirm(true);
    await expect(promise).resolves.toBe(true);
  });

  test("escapes the message + subtitle + okLabel for HTML safety", () => {
    appConfirm("<script>x", "<b>y</b>", { okLabel: "<i>OK</i>" });
    expect(dialog().innerHTML).toContain("&lt;script&gt;x");
    expect(dialog().innerHTML).toContain("&lt;b&gt;y&lt;/b&gt;");
    expect(dialog().innerHTML).toContain("&lt;i&gt;OK&lt;/i&gt;");
    resolveConfirm(false);
  });
});

describe("appPrompt", () => {
  test("renders an input + resolves with trimmed value on OK", async () => {
    const promise = appPrompt("Album name");
    const input = /** @type {HTMLInputElement} */ (document.getElementById("confirm-input"));
    expect(input).toBeTruthy();
    input.value = "  My Album  ";
    resolveConfirm(true);
    await expect(promise).resolves.toBe("My Album");
  });

  test("resolves null on Cancel", async () => {
    const promise = appPrompt("Album name");
    /** @type {HTMLInputElement} */ (document.getElementById("confirm-input")).value = "ignored";
    resolveConfirm(false);
    await expect(promise).resolves.toBeNull();
  });

  test("Enter key in the input fires OK", async () => {
    const promise = appPrompt("Album name");
    const input = /** @type {HTMLInputElement} */ (document.getElementById("confirm-input"));
    input.value = "Vacation";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await expect(promise).resolves.toBe("Vacation");
  });

  test("seeds initial value when supplied", () => {
    appPrompt("Rename", { value: "Old name", placeholder: "..." });
    const input = /** @type {HTMLInputElement} */ (document.getElementById("confirm-input"));
    expect(input.value).toBe("Old name");
    expect(input.placeholder).toBe("...");
  });
});
