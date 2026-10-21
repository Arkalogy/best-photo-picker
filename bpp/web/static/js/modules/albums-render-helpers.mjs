// @ts-check
/**
 * Smart-album sidebar icon + tooltip lookups, split out of
 * albums-render.mjs for the 500-LOC cap. Pure functions of the
 * album type — no DOM, no shared state.
 */

/**
 * @param {any} album - an album object (preferred) or an album type
 * @param {Record<string, string>} ICONS
 * @returns {string}
 */
export function smartAlbumIcon(album, ICONS) {
  // The server sends every album's icon key (a plugin type's included,
  // from SmartAlbumRegistry.presentation); a bare type string, or an
  // album without one, gets the folder.
  const key = album && typeof album === "object" ? album.icon : "";
  return (key && ICONS[key]) || ICONS.folder;
}

/**
 * @param {any} album - an album object (preferred) or an album type
 * @returns {string}
 */
export function smartAlbumTip(album) {
  return album && typeof album === "object" && album.tooltip ? album.tooltip : "";
}
