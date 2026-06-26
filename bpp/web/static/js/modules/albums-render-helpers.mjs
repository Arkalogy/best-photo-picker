// @ts-check
/**
 * Smart-album sidebar icon + tooltip lookups, split out of
 * albums-render.mjs for the 500-LOC cap. Pure functions of the
 * album type — no DOM, no shared state.
 */

/**
 * @param {string} albumType
 * @param {Record<string, string>} ICONS
 * @returns {string}
 */
export function smartAlbumIcon(albumType, ICONS) {
  return (        albumType === "smart_score"
          ? ICONS.star
          : albumType === "smart_recent"
            ? ICONS.clock
            : albumType === "smart_unsorted"
              ? ICONS.inbox
              : albumType === "smart_video"
                ? ICONS.video
                : albumType === "smart_screenshot"
                  ? ICONS.screenshot
                  : albumType === "smart_moments"
                    ? ICONS.moments
                    : albumType === "smart_duplicates"
                      ? ICONS.duplicate
                      : albumType === "smart_no_faces"
                      ? ICONS.noFace
                      : albumType === "smart_document"
                        ? ICONS.document
                        : albumType === "smart_edited"
                          ? ICONS.pencil
                          : albumType === "smart_pet"
                            ? ICONS.paw
                            : albumType === "smart_hidden"
                              ? ICONS.hidden
                              : albumType === "smart_tag"
                                ? ICONS.tag
                                : ICONS.folder
  );
}

/**
 * @param {string} albumType
 * @returns {string}
 */
export function smartAlbumTip(albumType) {
  return (        albumType === "smart_score"
          ? "Top 10% by BPP score"
          : albumType === "smart_recent"
            ? "Imported in the last 7 days"
            : albumType === "smart_unsorted"
              ? "Photos not yet added to any album"
              : albumType === "smart_moments"
                ? "Bursts of visually-similar shots taken close together — review and keep the best"
                : albumType === "smart_no_faces"
                  ? "Photos where face detection found no faces — may include small or partially hidden faces"
                  : ""
  );
}
