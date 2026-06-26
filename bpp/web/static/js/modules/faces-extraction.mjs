// @ts-check
/**
 * Face extraction lifecycle — start / retry / re-extract entry points
 * and the SSE progress listener. Split out of faces.mjs when the LOC
 * gate caught it crossing the 500-line cap (2026-06-12); faces.mjs
 * re-exports everything here, so callers and the window bridge are
 * unchanged.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { startClipExtraction } from "./clip.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { getSetting } from "./settings-client.mjs";
import { escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import { appConfirm } from "./dialogs.mjs";
import { loadFaceClusters } from "./faces.mjs";

/**
 * Kick off CLIP extraction if the user has it enabled and it's available.
 * Used by analysis.js after analyze completes.
 *
 * @returns {Promise<boolean>} true if extraction was started
 */
export async function _maybeStartClip() {
  try {
    const clipToggle = getSetting("model_clip", "true");
    if (clipToggle === "false" || clipToggle === "0") return false;
    const st = await apiFetch("/api/v1/status");
    if ((st.clip_available || st.clip_installable) && !st.clip_ready && !st.clip_extracting) {
      startClipExtraction();
      return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

export async function startFaceExtraction() {
  /** @type {any} */
  const win = window;
  if (!win.faceRecognitionAvailable) {
    toast("Face recognition requires the faces extra: pip install bppicker[faces]", true);
    return;
  }

  // ML download consent — same gate as Analyze All (analysis.mjs).
  // Face models are downloaded on first use; ask the user before any
  // network call goes out.
  try {
    const data = await apiFetch("/api/v1/models/pending");
    const pending = (data && data.models) || [];
    if (pending.length > 0) {
      const totalMb = data.total_mb || 0;
      const totalLabel =
        totalMb >= 100 ? `${Math.round(totalMb)} MB` : `${totalMb.toFixed(1)} MB`;
      const rows = pending
        .map((m) => {
          const sizeLabel =
            m.size_mb >= 1
              ? `${Math.round(m.size_mb)} MB`
              : `${(m.size_mb * 1024).toFixed(0)} KB`;
          return `<li>
            <span class="ml-name">${escapeAttr(m.name)}</span>
            <span class="ml-meta">${sizeLabel} · ${escapeAttr(m.host)}</span>
          </li>`;
        })
        .join("");
      const bodyHTML = `<ul class="ml-consent-list">${rows}</ul>
        <p class="confirm-sub ml-consent-foot">Total: ${escapeAttr(totalLabel)}.
          Cached at ~/.cache/bpp. Runs on-device — no photos leave your machine.</p>`;
      const ok = await win.appConfirm?.(
        "Download ML models?",
        null,
        { okLabel: "Download and extract", bodyHTML },
      );
      if (!ok) return;
    }
  } catch (e) {
    console.warn("Failed to load model manifest:", e);
    const ok = await win.appConfirm?.(
      "Download ML models?",
      "Best Photo Picker will download ML models for face detection the first time it needs them. "
        + "Cached at ~/.cache/bpp. Runs on-device — no data leaves your machine.",
      { okLabel: "Continue", cancelLabel: "Cancel" },
    );
    if (!ok) return;
  }

  let resp;
  try {
    resp = await apiFetch("/api/v1/faces/extract", { method: "POST" });
  } catch (e) {
    toastError("start face extraction", e);
    return;
  }
  if (resp.error) {
    toastError("extract faces", new Error(resp.error));
    return;
  }
  listenFaceProgress();
}

export function listenFaceProgress() {
  /** @type {any} */
  const win = window;
  /** @type {number | null} */
  let faceExtractStart = null;
  const src = authEventSource("/api/v1/faces/extract/progress");
  src.onmessage = (ev) => {
    const msg = /** @type {any} */ (parseSSE(ev.data));
    if (!msg) return;
    if (msg.type === "progress") {
      if (!faceExtractStart) faceExtractStart = Date.now();
      const pct = ((msg.current / msg.total) * 100).toFixed(0);
      let eta = "";
      if (msg.current > 2) {
        const elapsed = (Date.now() - faceExtractStart) / 1000;
        const rate = msg.current / elapsed;
        const remaining = Math.round((msg.total - msg.current) / rate);
        if (remaining >= 60) eta = ` — ~${Math.round(remaining / 60)}m left`;
        else if (remaining > 5) eta = ` — ~${remaining}s left`;
      }
      win.showStatusProgress?.(`Detecting faces ${msg.current}/${msg.total}${eta}`, pct);
    } else if (msg.type === "done") {
      src.close();
      win.hideStatusProgress?.();
      loadFaceClusters(true);
    } else if (msg.type === "error") {
      src.close();
      win._analyzeStop?.();
      win.hideStatusProgress?.();
      toastError("extract faces", new Error(msg.message || "unknown error"));
    } else if (msg.type === "start") {
      faceExtractStart = Date.now();
      win.showStatusProgress?.(`Found ${msg.total} images with faces…`, 0);
    } else if (msg.type === "warning") {
      toast(msg.message || "Warning during face extraction", true);
    }
  };
  src.onerror = () => {
    src.close();
    win.hideStatusProgress?.();
  };
}

export async function retryFaceExtraction() {
  let resp;
  try {
    resp = await apiFetch("/api/v1/faces/retry", { method: "POST" });
  } catch (e) {
    toastError("retry face extraction", e);
    return;
  }
  if (resp.error) {
    toastError("retry face extraction", new Error(resp.error));
    return;
  }
  listenFaceProgress();
}

/**
 * Confirm-gated entry point for the analyze split-button menu. Re-extract
 * wipes every stored face embedding + manual person tag and rebuilds from
 * scratch, so it must never fire on a stray click. Person *names* survive
 * (re-bound to the best-overlapping new cluster); cluster numbers don't.
 */
export async function confirmRetryFaceExtraction() {
  const ok = await appConfirm(
    "Re-extract all faces?",
    "Wipes existing face data and re-detects every photo from scratch with the " +
      "latest detector — recovers faces older runs missed. Person names are kept. " +
      "On a large library this can take several minutes.",
    { okLabel: "Re-extract", okClass: "danger" }
  );
  if (!ok) return;
  try {
    await retryFaceExtraction();
  } catch (e) {
    toastError("start face re-extraction", e);
  }
}
