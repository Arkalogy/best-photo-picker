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
  if (total > 0) return `Downloading… ${Math.floor((done / total) * 100)}%`;
  if (done > 0) return `Downloading… ${(done / (1024 * 1024)).toFixed(1)} MB`;
  return "Downloading…";
}

/**
 * Poll a download job until it finishes. Callers POST the Download /
 * Redownload request themselves (inside their own try/catch + toastError)
 * and pass the ``job`` key from its response.
 * @param {string | undefined} jobKey
 * @param {{onProgress?: (snap: DownloadJobSnapshot) => void,
 *          intervalMs?: number, sleep?: (ms: number) => Promise<void>}} [opts]
 * @returns {Promise<DownloadJobSnapshot>} the finished job (with ``result``)
 */
export async function waitForDownloadJob(jobKey, opts = {}) {
  if (!jobKey) throw new Error("The server did not start a download");
  const intervalMs = opts.intervalMs ?? 1000;
  const sleep = opts.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  for (;;) {
    /** @type {DownloadJobSnapshot} */
    const snap = await apiFetch(
      "/api/v1/models/download-jobs/" + encodeURIComponent(jobKey),
    );
    if (snap.status === "done") return snap;
    if (snap.status === "error") throw new Error(snap.error || "Download failed");
    opts.onProgress?.(snap);
    await sleep(intervalMs);
  }
}
