// @ts-check
/**
 * JS-side mirror of the cluster sentinel values defined in
 * `bpp/constants.py`. The DB and backend treat these as magic ints,
 * but the frontend code should never type the raw integer — it
 * obscures intent and breaks if the sentinels ever change.
 *
 * If you change either value here, update `bpp/constants.py` to match
 * (and vice versa). A schema migration would be required for the DB
 * column to absorb the change.
 */

/** Face has been extracted but no cluster has claimed it yet. */
export const CLUSTER_UNASSIGNED = -1;

/** Face was dismissed — "not a real face" or hidden from the cluster. */
export const CLUSTER_DISMISSED = -2;

/**
 * Apple-Photos-style "significant person" threshold: clusters with
 * fewer than this many photos are hidden from the main People grid
 * unless the user has explicitly named them. Used by ``people.mjs``
 * (cluster filter) and ``people-view.mjs`` (significance gate).
 *
 * Pre-dedup the value was hardcoded as a local
 * ``const FACE_MIN_PHOTOS = 4;`` in BOTH modules; bumping it required
 * editing two places. Python mirror: FACE_MIN_PHOTOS in
 * ``bpp/constants.py`` (group detection's significance gate) — keep
 * the two in sync.
 */
export const FACE_MIN_PHOTOS = 4;
