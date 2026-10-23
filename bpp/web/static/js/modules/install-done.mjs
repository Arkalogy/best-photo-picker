// @ts-check
/**
 * What to tell the user when the in-app installer finishes. Once pip has
 * run, what it installed works after a restart (it's never loaded into the
 * running app), and further installs wait until then: the server sets
 * `restart_required`.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { removeNudge, showNudge } from "./nudges.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * What to say when an install finished: it works after a restart.
 * @param {string} what  e.g. "Face recognition"
 * @returns {string}
 */
export function installDoneMessage(what) {
  return `${what} installed — restart Best Photo Picker to use it`;
}

/**
 * Toast that the install finished (as a warning that stays up longer: it
 * asks for a restart) and keep the restart banner up — unless pip found it
 * all installed already and wrote nothing (no restart to wait for).
 * @param {string} what
 * @param {string} key  the install that finished (it now waits too)
 * @param {{restart_required?: boolean}} [done]  the stream's "done" event
 * @returns {boolean} true when it waits for a restart
 */
export function showInstallDone(what, key, done) {
  if (done?.restart_required === false) {
    toast(`${what} is already installed — if it still isn't available, restart Best Photo Picker`, "success", {
      duration: 12000,
    });
    void refreshInstallState();
    return false;
  }
  toast(installDoneMessage(what), "warning", { duration: 12000 });
  markRestartPending(_waitingWith(key));
  return true;
}

/**
 * What waits for the restart: the server's list as this page last heard
 * it (refreshInstallState keeps it current) plus *key* — the install this
 * page just ran, known at once (the page acts on it before the next
 * refresh) — or a placeholder when neither is known.
 * @param {string} [key]
 * @returns {string[]}
 */
function _waitingWith(key) {
  /** @type {string[]} */
  const known = /** @type {any} */ (window).installRestartPending || [];
  const keys = known.filter((k) => k !== "pending");
  if (key && !keys.includes(key)) keys.push(key);
  return keys.length ? keys : ["pending"];
}

/** Bumped whenever this page itself marks a restart pending. */
let _localChange = 0;

/**
 * Remember (for this page) that an install awaits a restart: stop
 * offering installs and keep a banner up until the app restarts.
 * @param {string[]} keys  what waits for it (the server's list, when known)
 */
export function markRestartPending(keys) {
  _localChange += 1; // an answer asked for before this is out of date
  /** @type {any} */
  const win = window;
  win.installRestartPending = keys;
  win.faceInstallable = false;
  showNudge("restart_to_finish_install", "nudge-container");
}

/**
 * Toast an install that couldn't start. "Restart first" isn't a failure
 * to retry, so it gets the server's own wording as a warning.
 * @param {string} action  e.g. "install face recognition"
 * @param {any} err  the error apiFetch threw
 * @returns {Promise<boolean> | undefined} the install-state refresh it
 *   started (a restart is pending: which install it is), to await
 */
export function toastInstallError(action, err) {
  if (err?.restartRequired) {
    // pip ran (and failed): retrying waits for a restart.
    toast(`Couldn't ${action}: ${err.message}. Restart Best Photo Picker, then try again.`, "warning", {
      duration: 12000,
    });
  } else if (err?.body?.context?.reason === "restart_pending") {
    toast(`Couldn't ${action}: ${err.body.error}.`, "warning", { duration: 12000 });
    markRestartPending(_waitingWith());
    return refreshInstallState(); // which install it is
  } else if (err?.body?.context?.reason === "busy") {
    // Analysis etc. is running: not a failure, just "not now".
    toast(`Couldn't ${action} yet: ${err.body.error}.`, "warning", { duration: 8000 });
  } else {
    toastError(action, err);
  }
  return undefined;
}

/**
 * A failed pip run still wrote files: if the error event says a restart
 * is needed, show the restart banner too.
 * @param {{restart_required?: boolean}} msg  the "error" event
 * @param {string} key  the install it's about
 */
export function noteInstallError(msg, key) {
  if (msg.restart_required) markRestartPending(_waitingWith(key));
}

/**
 * The Error an install flow rejects with for an "error" event; it
 * carries whether a restart is now needed (for toastInstallError).
 * @param {{message?: string, restart_required?: boolean}} msg
 */
export function installEventError(msg) {
  return Object.assign(new Error(msg.message || "Install failed"), {
    restartRequired: !!msg.restart_required,
  });
}

/**
 * Show an install state from the server (GET /install/state's shape; the
 * page's first /status carries the same, so startup needs no second call).
 * @param {{restart_pending?: string[], face_installable?: boolean,
 *   face_license_needed?: boolean, install_blocked?: string,
 *   interrupted?: string[], interrupted_keys?: string[]}} state
 * @returns {boolean} true when a restart is pending
 */
export function applyInstallState(state) {
  /** @type {any} */
  const win = window;
  win.faceInstallable = !!state.face_installable;
  win.installBlocked = state.install_blocked || "";
  win.faceLicenseNeeded = !!state.face_license_needed;
  win.faceWaitingForRestart = (state.restart_pending || []).includes("faces");
  win.interruptedInstalls = state.interrupted_keys || [];
  showInterruptedInstalls(state.interrupted || [], win.interruptedInstalls);
  if (state.restart_pending?.length) {
    markRestartPending(state.restart_pending);
    return true;
  }
  if (win.installRestartPending?.length) {
    // The app was restarted under this page: nothing waits any more.
    win.installRestartPending = [];
    removeNudge("restart_to_finish_install");
  }
  return false;
}

/**
 * Ask the server whether an install is waiting for a restart (it may
 * have finished out of view: dialog closed, another tab) and show the
 * banner if so. Never throws.
 * @returns {Promise<boolean>} true when a restart is pending
 */
export async function refreshInstallState() {
  const asked = _localChange;
  try {
    const state = await apiFetch("/api/v1/install/state");
    // This page learned of an install meanwhile (its "done"): the answer
    // predates it — the next poll brings the current one.
    if (asked !== _localChange) return !!(/** @type {any} */ (window).installRestartPending?.length);
    return applyInstallState(state);
  } catch {
    /* server busy or down: the next poll tries again */
  }
  return false;
}

/**
 * Before showing an install's consent dialog: refuse up front (with the
 * banner and a warning) when a restart is pending, instead of after the
 * user has agreed.
 * @returns {Promise<boolean>} true when the install may go ahead
 */
export async function installAllowedNow() {
  if (await refreshInstallState()) {
    toast("Restart Best Photo Picker to finish the last install first.", "warning", {
      duration: 12000,
    });
    return false;
  }
  const blocked = /** @type {any} */ (window).installBlocked;
  if (blocked) {
    // Analysis running, a download, another install: not now (the server
    // would refuse it after the consent dialog).
    toast(`Can't install yet: ${blocked}.`, "warning", { duration: 8000 });
    return false;
  }
  return true;
}

const _INTERRUPTED_ID = "nudge-install_interrupted";
/** Installs the app itself can run again: faces (analyze prompt), inpaint. */
const _IN_APP_REPAIR = new Set(["faces", "inpaint"]);

/**
 * Installs pip was cut off in (the app quit, the deadline, a closed page):
 * packages may be half replaced. Say so with each repair, keep the text
 * current as they're fixed, and remove the banner once none is left.
 * @param {string[]} commands  pip commands to repair ("" if unknown)
 * @param {string[]} [keys]  which installs (the app can redo faces and inpaint)
 */
export function showInterruptedInstalls(commands, keys = []) {
  let banner = document.getElementById(_INTERRUPTED_ID);
  if (!commands.length) {
    banner?.remove();
    return;
  }
  const known = commands.filter(Boolean);
  // Offer the in-app route only where the app has one (the face install
  // prompt, the AI removal Download button).
  const inApp = keys.length > 0 && keys.every((k) => _IN_APP_REPAIR.has(k));
  const terminal = known.length ? `run this in a terminal: ${known.join("  ;  ")}` : "";
  const repair =
    inApp && terminal
      ? `install it again from the app, or ${terminal}`
      : inApp || !terminal
        ? "install it again from the app"
        : terminal;
  const text =
    "A package install was stopped before it finished, so some features " +
    `may not work. To repair it, ${repair}`;
  if (!banner) {
    const container = document.getElementById("nudge-container");
    if (!container) return;
    banner = document.createElement("div");
    banner.id = _INTERRUPTED_ID;
    banner.className = "nudge-banner nudge-visible";
    banner.setAttribute("role", "alert");
    const msg = document.createElement("span");
    msg.className = "nudge-msg";
    banner.appendChild(msg);
    container.prepend(banner);
  }
  const msg = /** @type {HTMLElement} */ (banner.querySelector(".nudge-msg"));
  // textContent: the commands are shown as text, never as HTML.
  if (msg.textContent !== text) msg.textContent = text;
}

/**
 * Follow an install's progress stream (after its POST). Calls *onLog* for
 * each pip line. Resolves with the "done" event; rejects with
 * {@link installEventError} (an "error" event, after noting a pending
 * restart) or a "connection lost" Error. Either way the install state is
 * re-read, so the banners are current.
 * @param {string} key  install key ("faces", "inpaint", …)
 * @param {(line: string) => void} [onLog]
 * @returns {Promise<any>}
 */
export function followInstall(key, onLog) {
  return new Promise((resolve, reject) => {
    const src = authEventSource(`/api/v1/install/${key}/progress`);
    let started = false; // the server sends "start" before running pip
    src.onmessage = (ev) => {
      const msg = /** @type {any} */ (parseSSE(ev.data));
      if (!msg) return;
      started = true;
      if (msg.type === "log") {
        onLog?.(String(msg.message || ""));
      } else if (msg.type === "done") {
        src.close();
        void refreshInstallState(); // a "stopped" banner may be fixed now
        resolve(msg);
      } else if (msg.type === "error") {
        src.close();
        noteInstallError(msg, key);
        void refreshInstallState();
        reject(installEventError(msg));
      }
    };
    src.onerror = () => {
      src.close();
      // The server still checks what pip did: show a pending restart now.
      void refreshInstallState();
      // Refused before it began (it took too long to start, or another tab
      // is running it): pip never ran.
      const why = started
        ? "Connection lost — the install may not have finished"
        : "The install didn't start (it took too long, or it's already running elsewhere)";
      reject(new Error(why));
    };
  });
}
