// @ts-check
/** Shared DOM + fetch helpers for the install-flow tests. */
import { afterEach, beforeEach, vi } from "vitest";

/** Give each test the toast + nudge containers, and clean up after it. */
export function useInstallDom() {
  beforeEach(() => {
    document.body.innerHTML = `<div id="toast-container"></div><div id="nudge-container"></div>`;
  });
  afterEach(() => {
    document.body.innerHTML = "";
    vi.unstubAllGlobals();
    delete (/** @type {any} */ (window).faceInstallable);
    delete (/** @type {any} */ (window).installRestartPending);
    delete (/** @type {any} */ (window).faceWaitingForRestart);
  });
}

/** @returns {string} the persistent restart banner's text, or "" */
export function restartBanner() {
  return document.getElementById("nudge-restart_to_finish_install")?.textContent || "";
}

/** @param {object} body  what every fetch answers */
export function stubState(body) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    )
  );
}

/** @returns {string[]} toast class names + text */
export function toasts() {
  return [...document.querySelectorAll("#toast-container .toast")].map(
    (el) => `${el.className}: ${el.textContent}`
  );
}
