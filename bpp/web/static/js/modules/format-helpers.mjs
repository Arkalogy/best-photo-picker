// @ts-check
/**
 * Pure formatting + parsing helpers used across the UI.
 *
 * Bridged onto `window` via index.html's module bootstrap so existing
 * script-tag code keeps working unchanged. New code should `import`
 * these names directly.
 */

/**
 * Format a slider value for display next to the slider track.
 *
 * Integer params (thresholds, counts, windows) render as plain integers;
 * everything else renders to two decimal places.
 *
 * @param {string} param - Parameter key (e.g. "blur_weight", "max_per_day").
 * @param {string|number} val - Raw slider value.
 * @returns {string} Display-ready string.
 */
export function formatVal(param, val) {
  const INT_PARAMS = [
    "hash_distance_threshold",
    "time_window_seconds",
    "max_per_day",
    "min_per_month",
    "max_per_month",
    "global_hash_distance_threshold",
  ];
  if (INT_PARAMS.includes(param)) {
    return Math.round(Number(val)).toString();
  }
  return parseFloat(String(val)).toFixed(2);
}

/**
 * Format a byte count as a short human-readable string (B / KB / MB / GB).
 *
 * Uses base-1024 (KB = 1024 B) to match Finder / file managers — *not*
 * decimal SI prefixes. Bytes get no decimal; KB/MB/GB get one.
 *
 * @param {number} bytes
 * @returns {string}
 */
export function _formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + " " + units[i];
}

/**
 * Safely parse a JSON payload from a Server-Sent Events `data:` line.
 *
 * Returns null on bad input rather than throwing — SSE streams can
 * occasionally deliver garbled lines and we'd rather drop them than
 * blow up the whole consumer.
 *
 * @param {string} data
 * @returns {unknown}
 */
export function parseSSE(data) {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
