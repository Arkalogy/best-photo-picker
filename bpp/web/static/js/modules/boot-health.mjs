// @ts-check
/**
 * Surface the work the server picks up on its own at start-up, which
 * otherwise runs with no sign in the UI:
 *
 * - an interrupted face extraction resuming (every face edit, Analyze
 *   and Import answer 409 "resuming" until it finishes) → the status
 *   bar says so while it runs, and a failed resume gets a toast with
 *   the next step;
 * - a missing-file scan the server refused (more than half the library
 *   gone at once: an unplugged drive, not a real deletion) → a toast
 *   telling the user how to recheck once the files are back.
 *
 * Reads /api/v1/health (the owner-only diagnostic block) and stops
 * polling as soon as nothing is in flight. Idempotent.
 */

import { apiFetch } from "./api-client.mjs";
import { hideStatusProgress, showStatusProgress } from "./analysis-status.mjs";
import { toast } from "./toast.mjs";

let _polling = false;
const _POLL_MS = 5000;

/** Status-bar text while an interrupted face extraction resumes. */
export const RESUME_TEXT = "Finishing face grouping from last time…";

/**
 * What the page should do for one health snapshot: pure, so every
 * branch is unit-tested without a DOM.
 *
 * @param {any} health  the /api/v1/health body
 * @param {{ resumeShown: boolean, resumeFailedTold: boolean, refusedTold: boolean }} seen
 * @returns {{ showResume: boolean, hideResume: boolean, toasts: string[], keepPolling: boolean }}
 */
export function planBootHealth(health, seen) {
  const checks = (health && health.checks) || {};
  const recovery = checks.face_recovery || {};
  const scan = checks.missing_scan || {};
  const toasts = [];
  const inFlight = !!recovery.in_flight;
  const showResume = inFlight && !seen.resumeShown;
  const hideResume = !inFlight && seen.resumeShown;
  if (recovery.failed && !seen.resumeFailedTold) {
    toasts.push(
      "Couldn't finish the face grouping from last time: open the analyze menu " +
        "(▾ next to Re-analyze) and choose Re-extract faces to run it again (details in server.log)"
    );
  }
  if (scan.refused && !seen.refusedTold) {
    const n = typeof scan.missing === "number" ? `${scan.missing} of ${scan.total} ` : "";
    toasts.push(
      `${n}photos couldn't be found, so nothing was marked missing. If a drive is ` +
        "unplugged, reconnect it; the app rechecks on its own (restart it to recheck now)"
    );
  }
  return { showResume, hideResume, toasts, keepPolling: inFlight };
}

/**
 * Poll /api/v1/health until the start-up work is done, mirroring it
 * into the status bar and toasts. A second call while polling is a no-op.
 */
export async function monitorBootHealth() {
  if (_polling) return;
  _polling = true;
  /** @type {any} */
  const win = window;
  const seen = { resumeShown: false, resumeFailedTold: false, refusedTold: false };
  try {
    while (true) {
      let health;
      try {
        health = await apiFetch("/api/v1/health");
      } catch {
        break; // server gone / transient — stop quietly
      }
      const plan = planBootHealth(health, seen);
      // Analyze owns the shared progress bar while it runs.
      if (plan.showResume && win.activeOperation !== "analyze") {
        showStatusProgress(RESUME_TEXT, 0);
        seen.resumeShown = true;
      }
      if (plan.hideResume) {
        if (win.activeOperation !== "analyze") hideStatusProgress();
        seen.resumeShown = false;
      }
      for (const text of plan.toasts) toast(text);
      if (plan.toasts.length) {
        seen.resumeFailedTold = seen.resumeFailedTold || !!health?.checks?.face_recovery?.failed;
        seen.refusedTold = seen.refusedTold || !!health?.checks?.missing_scan?.refused;
      }
      if (!plan.keepPolling) break;
      await new Promise((r) => setTimeout(r, _POLL_MS));
    }
  } finally {
    _polling = false;
  }
}
