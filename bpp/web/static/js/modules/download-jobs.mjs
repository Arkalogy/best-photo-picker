// @ts-check
/**
 * Background model downloads. The Download / Redownload endpoints answer
 * at once with a job (the fetch can take minutes — longer than a browser
 * request stays open) and the caller polls the job here for progress.
 */

import { apiFetch } from "./api-client.mjs";

/**
 * @typedef {{status?: string, bytes_done?: number, bytes_total?: number | null,
 *            result?: any, error?: string | null, job?: string}} DownloadJobSnapshot
 */

/**
 * Row/button label for a running download.
 * @param {DownloadJobSnapshot} snap
 * @returns {string}
 */
export function downloadProgressText(snap) {
  const done = snap.bytes_done || 0;
  const total = snap.bytes_total || 0;
  // Every byte in: the server is checking (and maybe unpacking) the file.
  if (total > 0 && done >= total) return "Finishing… (checking the download)";
  if (total > 0) return `Downloading… ${Math.floor((done / total) * 100)}%`;
  if (done > 0) return `Downloading… ${(done / (1024 * 1024)).toFixed(1)} MB`;
  return "Downloading…";
}

/**
 * Poll a download job until it finishes. Callers POST the Download /
 * Redownload request themselves (inside their own try/catch + toastError)
 * and pass the ``job`` key from its response. It never gives up while the
 * server's job runs (giving up would unlock a row whose files are still
 * being written) — a failed check is said at once through ``onRetry`` and
 * retried; only an answer that won't change (a 4xx such as "no such job":
 * the server restarted, nothing is running) ends the wait.
 * When it makes no progress for ``stallMs`` (5 minutes) it says so, once,
 * through ``onStall`` —
 * the row stays locked, honestly — also once every byte is in ("Finishing…"),
 * should checking or unpacking the file hang.
 * @param {string | undefined} jobKey
 * @param {{onProgress?: (snap: DownloadJobSnapshot) => void,
 *          onStall?: (snap: DownloadJobSnapshot) => void,
 *          onRetry?: (err: Error) => void,
 *          intervalMs?: number, stallMs?: number, now?: () => number,
 *          sleep?: (ms: number) => Promise<void>}} [opts]
 * @returns {Promise<DownloadJobSnapshot>} the finished job (with ``result``)
 */
export async function waitForDownloadJob(jobKey, opts = {}) {
  if (!jobKey) throw new Error("The server did not start a download");
  const intervalMs = opts.intervalMs ?? 1000;
  const stallMs = opts.stallMs ?? 5 * 60 * 1000;
  const now = opts.now ?? (() => Date.now());
  const sleep = opts.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  let lastBytes = -1;
  let lastChange = now();
  /** @type {DownloadJobSnapshot} */
  let snap = {};
  let stalled = false;
  /** Say a stall once, when it starts (not on every check). */
  const checkStall = () => {
    const isStalled = now() - lastChange >= stallMs;
    if (isStalled && !stalled) opts.onStall?.(snap);
    stalled = isStalled;
    return stalled;
  };
  for (;;) {
    try {
      snap = await apiFetch("/api/v1/models/download-jobs/" + encodeURIComponent(jobKey));
    } catch (err) {
      if (_wontChange(err)) throw err;
      // The job may still be running: say so (the stall clock is about
      // progress, not reachability — it waits), and check again.
      opts.onRetry?.(/** @type {Error} */ (err));
      await sleep(intervalMs);
      continue;
    }
    if (snap.status === "done") return snap;
    if (snap.status === "error") throw new Error(snap.error || "Download failed");
    const bytes = snap.bytes_done || 0;
    if (bytes !== lastBytes) {
      lastBytes = bytes;
      lastChange = now();
    }
    // (Elapsed time, not polls: a slow server stretches each poll.)
    if (!checkStall()) opts.onProgress?.(snap);
    await sleep(intervalMs);
  }
}

/**
 * Whether a failed progress check would fail the same way next time: a
 * client-error answer, other than the ones a retry can clear (timeout,
 * too early, too many requests). Anything else — no connection, a server
 * error, a garbled or cut-short body, a cancelled fetch — is retried:
 * giving up would unlock a row whose files may still be being written.
 * @param {unknown} err
 * @returns {boolean}
 */
function _wontChange(err) {
  const status = /** @type {{status?: number}} */ (err)?.status;
  return typeof status === "number" && status >= 400 && status < 500 && ![408, 425, 429].includes(status);
}

/**
 * Row label while a progress check fails and is retried.
 * @param {Error} err
 * @returns {string}
 */
export function downloadRetryText(err) {
  return `Can't reach the server (${err.message || err}) — retrying…`;
}

/**
 * Row label for a download that has stopped making progress. (Restarting
 * is the way out: it also stops the server's job — reloading the page
 * would only unlock the row while the job may still write.)
 */
export const DOWNLOAD_STALLED_TEXT =
  "No progress for several minutes — still waiting on the server (if it doesn't move, restart Best Photo Picker)";

/** The same, short enough for a button. */
export const DOWNLOAD_STALLED_SHORT = "Stalled — still waiting";
