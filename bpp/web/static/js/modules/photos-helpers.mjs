// @ts-check
/**
 * Small pure helpers for the photo grid. Extracted from photos.mjs to
 * keep it under the 500-LOC cap; photos.mjs re-exports both so callers
 * are unchanged.
 */

/**
 * Count selectedPaths that are visible in the current scope (album / library).
 *
 * `selectedPaths` is a process-global set tracking every photo the user has
 * picked across the entire library. The status-bar subtitle and the
 * "BPP Picks (N)" toolbar pill should only count picks within the CURRENT
 * view's photos — otherwise an album with 24 photos can read "50 selected
 * of 24" if the library has 50 picks library-wide.
 *
 * @param {Iterable<{filepath?: string, deleted_at?: string | null}>} photos
 * @param {Set<string> | null | undefined} selectedPaths
 * @returns {number}
 */
export function countSelectedInScope(photos, selectedPaths) {
  if (!selectedPaths || selectedPaths.size === 0) return 0;
  let count = 0;
  for (const p of photos) {
    if (p.deleted_at) continue;
    if (p.filepath && selectedPaths.has(p.filepath)) count++;
  }
  return count;
}

/**
 * @param {number} seconds
 * @returns {string}
 */
export function _formatDuration(seconds) {
  const s = Math.round(seconds);
  if (s < 3600) return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  return (
    Math.floor(s / 3600) +
    ":" +
    String(Math.floor((s % 3600) / 60)).padStart(2, "0") +
    ":" +
    String(s % 60).padStart(2, "0")
  );
}
