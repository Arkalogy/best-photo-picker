// @ts-check
/**
 * Surface the background perceptual-hash backfill in the status-bar
 * progress bar.
 *
 * The backfill (decode + hash every un-hashed photo, to power
 * near-duplicate detection) used to run completely silently while
 * pegging the machine. The server-side fix capped its worker pool and
 * exposes live progress at /api/v1/status.phash_progress; this poller
 * renders it so the user can actually SEE "Computing photo similarity
 * N/M" instead of wondering why the fans are spinning.
 *
 * It self-stops: once it has seen the backfill running and then finish,
 * it hides the bar, refreshes smart albums (so the Duplicates album
 * appears), and stops polling. If no backfill is running at all, it
 * gives up after a few idle ticks.
 */

import { apiFetch } from "./api-client.mjs";
import { hideStatusProgress, showStatusProgress } from "./analysis-status.mjs";
import { refreshSmartAlbums } from "./faces.mjs";

let _polling = false;
const _POLL_MS = 2000;
const _MAX_IDLE_TICKS = 5; // ~10s grace for a lazy backfill start to appear

/**
 * Poll /api/v1/status and mirror phash backfill progress into the
 * status-bar progress bar until it completes. Idempotent — a second
 * call while already polling is a no-op.
 */
export async function monitorPhashBackfill() {
  if (_polling) return;
  _polling = true;
  /** @type {any} */
  const win = window;
  let seenRunning = false;
  let idle = 0;
  try {
    while (true) {
      let st;
      try {
        st = await apiFetch("/api/v1/status");
      } catch {
        break; // server gone / transient — stop quietly
      }
      const p = st && st.phash_progress;
      if (p && p.running) {
        seenRunning = true;
        idle = 0;
        // Don't fight an active analyze for the shared progress bar —
        // analyze owns it; we only drive it when nothing else is.
        if (win.activeOperation !== "analyze") {
          const pct = p.total > 0 ? ((p.done / p.total) * 100).toFixed(0) : "0";
          showStatusProgress(`Computing photo similarity ${p.done}/${p.total}`, pct);
        }
      } else if (seenRunning) {
        // Just finished: clear the bar + reveal the Duplicates album.
        if (win.activeOperation !== "analyze") hideStatusProgress();
        try {
          await refreshSmartAlbums();
        } catch {
          /* non-fatal — albums refresh on next navigation anyway */
        }
        break;
      } else if (++idle >= _MAX_IDLE_TICKS) {
        break; // nothing running; stop polling
      }
      await new Promise((r) => setTimeout(r, _POLL_MS));
    }
  } finally {
    _polling = false;
  }
}
