// @ts-check
/**
 * Moments — gallery-entity helpers.
 *
 * A Moment is a group of visually-similar shots taken near in time
 * (moment_cluster_id / moment_size, populated by bpp/db/moments.py). In the
 * gallery the cards of one Moment share an accent colour so the group reads
 * as a single entity in place, and the keeper (best shot) gets a star.
 *
 * Pure functions only — unit-tested without the DOM.
 */

/**
 * Moment-specific keeper score. Within near-identical shots the scene and
 * composition are ~constant, so the global pick score adds noise; what
 * actually differs frame-to-frame is sharpness and face quality. (Eyes-open /
 * expression is a Phase-4 refinement once it's surfaced per photo.)
 * @param {any} p
 * @returns {number}
 */
export function momentScore(p) {
  const sharp = p.blur_score || 0; // "blur_score" is the sharpness score (higher = sharper)
  const face = p.face_score || 0;
  if (face > 0) return sharp * 0.55 + face * 0.45;
  // No face in frame → rank on sharpness, with the overall score as tiebreak.
  return sharp * 0.7 + (p.aggregate_score || 0) * 0.3;
}

/**
 * Group photos by Moment and return the keeper filepath for each multi-photo
 * Moment.
 * @param {any[]} photos
 * @returns {Set<string>}
 */
export function computeMomentKeepers(photos) {
  /** @type {Map<number, {fp: string, score: number}>} */
  const best = new Map();
  for (const p of photos || []) {
    const mid = p.moment_cluster_id || 0;
    if (!mid || (p.moment_size || 1) < 2) continue;
    const s = momentScore(p);
    const cur = best.get(mid);
    if (!cur || s > cur.score) best.set(mid, { fp: p.filepath, score: s });
  }
  /** @type {Set<string>} */
  const keepers = new Set();
  for (const { fp } of best.values()) keepers.add(fp);
  return keepers;
}

/**
 * Deterministic hue (0-359) for a Moment so all its cards share one accent.
 * @param {number} momentClusterId
 * @returns {number}
 */
export function momentAccentHue(momentClusterId) {
  // *47 is coprime with 360, so consecutive moment ids land far apart on the
  // wheel — adjacent groups in the grid get visibly distinct accents.
  return ((Number(momentClusterId) || 0) * 47) % 360;
}

/**
 * Class suffix for a photo card's Moment state. Centralized so the three
 * render paths (renderCardHTML + the two in-place updaters) stay in sync.
 *
 * Returns "" for non-Moment photos, else " in-moment moment-a|moment-b
 * [moment-keeper]". The a/b parity (from the moment id, which is assigned in
 * date order) alternates two calm shades so adjacent Moments are separable
 * without a rainbow. moment-keeper marks the best shot — CSS keeps it bright
 * and dims the prune-candidates.
 *
 * @param {any} p
 * @param {Set<string>} [momentKeepers]
 * @returns {string}
 */
export function momentClasses(p, momentKeepers) {
  if ((p.moment_size || 1) <= 1) return "";
  const parity = (Number(p.moment_cluster_id) || 0) % 2 === 0 ? "moment-a" : "moment-b";
  let c = ` in-moment ${parity}`;
  if (momentKeepers && momentKeepers.has(p.filepath)) c += " moment-keeper";
  return c;
}
