// @ts-check
// Every dialog takes keyboard focus when it opens and gives it back to
// where it was when it closes — and is marked up as a dialog for assistive
// tech. One helper (dialog-focus.mjs) does it for all of them.
import fs from "node:fs";
import path from "node:path";

import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn(async (/** @type {string} */ url) =>
  url.startsWith("/api/v1/model-registry/acceptance/draft")
    ? { model_id: "m", model_display_name: "M", required_checkboxes: [] }
    : url.startsWith("/api/v1/libraries")
      ? { libraries: [] }
      : {}
);
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));

const { showModal, resolveModal } = await import("../bpp/web/static/js/modules/modal.mjs");
const { appConfirm, appPrompt, resolveConfirm } =
  await import("../bpp/web/static/js/modules/dialogs.mjs");
const { showLibraryPicker, hideLibraryPicker } =
  await import("../bpp/web/static/js/modules/library.mjs");
const acceptance = await import("../bpp/web/static/js/modules/face-embedders-acceptance.mjs");

/** @returns {HTMLButtonElement} */
function opener() {
  return /** @type {HTMLButtonElement} */ (document.getElementById("opener"));
}

beforeEach(() => {
  document.body.innerHTML = `
    <button id="opener">Open</button>
    <div id="modal-overlay"><div class="modal">
      <div id="modal-icon"></div><div id="modal-title"></div><div id="modal-body"></div>
      <div id="modal-actions"></div></div></div>
    <div id="confirm-overlay" aria-labelledby="confirm-message"><div class="confirm-dialog"></div></div>
    <div id="library-picker-overlay" class="hidden"><div class="library-picker-modal">
      <button class="library-picker-close" data-action="hideLibraryPicker">×</button>
      <div id="library-list"></div></div></div>
    <div id="fe-acceptance-overlay" style="display: none"><div id="fe-acceptance-body"></div>
      <button id="fe-accept-btn"></button>
      <button data-action="closeFaceEmbedderAcceptance">Cancel</button></div>
  `;
  opener().focus();
});

describe("focus moves into the dialog and back out", () => {
  test("the generic modal", async () => {
    const p = showModal("!", "Title", "Body", { confirm: "Do it" });
    const active = document.activeElement;
    expect(document.getElementById("modal-overlay")?.contains(active)).toBe(true);
    expect(active?.tagName).toBe("BUTTON");
    resolveModal(false);
    await p;
    expect(document.activeElement).toBe(opener());
  });

  test("the confirm dialog", async () => {
    const p = appConfirm("Delete?");
    expect(document.getElementById("confirm-overlay")?.contains(document.activeElement)).toBe(true);
    resolveConfirm(false);
    await p;
    expect(document.activeElement).toBe(opener());
  });

  test("the prompt dialog (its input first, as before)", async () => {
    const p = appPrompt("Name");
    expect(document.activeElement?.tagName).toBe("INPUT");
    resolveConfirm(null);
    await p;
    expect(document.activeElement).toBe(opener());
  });

  test("the library picker", () => {
    showLibraryPicker();
    expect(
      document.getElementById("library-picker-overlay")?.contains(document.activeElement)
    ).toBe(true);
    hideLibraryPicker();
    expect(document.activeElement).toBe(opener());
  });

  test("the license dialog", async () => {
    await acceptance.openFaceEmbedderAcceptance("m");
    expect(document.getElementById("fe-acceptance-overlay")?.contains(document.activeElement)).toBe(
      true
    );
    acceptance.closeFaceEmbedderAcceptance();
    expect(document.activeElement).toBe(opener());
  });

  test("Tab wraps inside an open dialog instead of reaching the page behind", async () => {
    const p = showModal("!", "Title", "Body", { confirm: "Do it" });
    const buttons = /** @type {HTMLElement[]} */ ([
      ...document.querySelectorAll("#modal-overlay button"),
    ]);
    buttons[buttons.length - 1].focus();
    document.activeElement?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })
    );
    expect(document.activeElement).toBe(buttons[0]);
    buttons[0].dispatchEvent(
      new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })
    );
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);
    resolveModal(false);
    await p;
  });

  test("a dialog opened from a menu item gives focus back to what opened the menu", async () => {
    const { menuOpened, menuClosed } = await import("../bpp/web/static/js/modules/menu-a11y.mjs");
    document.body.innerHTML += `
      <div class="ctx-menu" id="m"><div class="ctx-menu-item" id="item">Delete…</div></div>`;
    const menu = /** @type {HTMLElement} */ (document.getElementById("m"));
    menuOpened(menu, { trigger: opener() });
    expect(document.activeElement?.id).toBe("item");
    // The menus hide themselves BEFORE running the action (the opener can't
    // take focus here, so focus is still on the now-hidden item).
    menu.classList.add("hidden");
    menuClosed(menu);
    const p = appConfirm("Delete?");
    resolveConfirm(false);
    await p;
    expect(document.activeElement).toBe(opener());
  });

  test("Tab is swallowed in a dialog with no enabled control, and recovers from the overlay", async () => {
    // (insertAdjacentHTML, not innerHTML +=: that would rebuild the body and
    // detach the focused opener there'd be nothing to restore to.)
    document.body.insertAdjacentHTML(
      "beforeend",
      `<div id="log-overlay"><button disabled>Close</button></div>`
    );
    const { focusInto, restoreFocusFrom } =
      await import("../bpp/web/static/js/modules/dialog-focus.mjs");
    const overlay = /** @type {HTMLElement} */ (document.getElementById("log-overlay"));
    focusInto(overlay);
    expect(document.activeElement).toBe(overlay);
    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    overlay.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    restoreFocusFrom(overlay);
    expect(document.activeElement).toBe(opener());
  });

  test("a custom dialog body without an id'd message is still named", async () => {
    const { _showConfirmOverlay } = await import("../bpp/web/static/js/modules/dialogs.mjs");
    _showConfirmOverlay("<p>Installing face recognition…</p><button disabled>Close</button>");
    expect(document.getElementById("confirm-message")?.textContent).toBe(
      "Installing face recognition…"
    );
    resolveConfirm(false);
  });

  test("the confirm dialog is named by its message", async () => {
    const p = appConfirm("Delete this photo?");
    const overlay = document.getElementById("confirm-overlay");
    const name = overlay?.getAttribute("aria-labelledby") || "";
    expect(document.getElementById(name)?.textContent).toBe("Delete this photo?");
    resolveConfirm(false);
    await p;
  });

  test("closing a dialog that never took focus changes nothing", () => {
    hideLibraryPicker(); // never shown
    expect(document.activeElement).toBe(opener());
  });
});

describe("the page marks every overlay up for assistive tech", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../bpp/web/templates/index.html"), "utf8");
  /** @param {string} id */
  const tag = (id) => html.match(new RegExp(`<div[^>]*id="${id}"[^>]*>`))?.[0] || "";

  test.each([
    ["library-picker-overlay", 'role="dialog"'],
    ["slideshow-overlay", 'role="dialog"'],
    ["modal-overlay", 'role="dialog"'],
    ["confirm-overlay", 'role="alertdialog"'],
    ["fe-acceptance-overlay", 'role="dialog"'],
  ])("%s is a %s with aria-modal and a name", (id, role) => {
    const t = tag(id);
    expect(t).toContain(role);
    expect(t).toContain('aria-modal="true"');
    expect(t).toMatch(/aria-label(ledby)?="/);
  });

  test("the sidebar backdrop is hidden from assistive tech", () => {
    expect(tag("sidebar-overlay")).toContain('aria-hidden="true"');
  });

  test("the server-down notice is an announcement, not a modal nothing can focus", () => {
    const t = tag("server-down-overlay");
    expect(t).toContain('role="alert"');
    expect(t).not.toContain("aria-modal");
  });

  test("the lightbox menu no longer advertises keys the open menu uses to navigate", () => {
    const item = html.match(/<div[^>]*id="lb-ctx-include"[^>]*>[^<]*(<[^>]*>[^<]*)*?<\/div>/)?.[0];
    expect(item).toBeTruthy();
    expect(item).not.toMatch(/&uarr;|↑/);
  });
});
