// @ts-check
/**
 * The on-disk facts of a model picker row, read from its install record
 * and catalog flags. Split out of ``face-embedders-rowstate.mjs`` for the
 * 500-LOC cap.
 */

/**
 * Where the row's weights stand: the install record's files, the catalog
 * flags and the "on disk" verdict they add up to.
 *
 * @param {object} entry
 * @param {object | undefined} install
 * @param {any} live  the picker map's copy of the entry (or the entry)
 * @returns {{files: Array<object>, hasDownloadable: boolean, isCatalog: boolean,
 *   isCatalogEntry: boolean, onDisk: boolean}}
 */
export function _onDiskFacts(entry, install, live) {
  const files = Array.isArray(install?.files) ? install.files : [];
  const hasDownloadable = files.some((f) => f && f.name);
  const filesOnDisk = files.some((f) => f && f.exists && f.name);
  const isCatalog = !install;
  // Authoritative "weights come via the catalog ensure-weights endpoint"
  // signal from the backend. Independent of whether a (fileless) legacy
  // install record is attached — LaMa / NudeNet have BOTH a legacy row
  // (empty file list) AND catalog fetch, so the old `!install` inference
  // misrouted them and dropped their Download action.
  const isCatalogEntry = live?.is_catalog_entry === true;
  const catalogOnDisk = entry?.catalog_on_disk === true;
  // OR, not "isCatalog && catalogOnDisk": catalog_on_disk is the real
  // on-disk truth for catalog entries and is false for non-catalog ones,
  // so consult it regardless of whether a legacy record is attached.
  // The old guard ignored it whenever a fileless legacy row existed,
  // leaving LaMa / NudeNet stuck at "Not downloaded" even once fetched.
  // Weights shipped inside an installed library (the Haar cascade in
  // opencv-python) are always present.
  const onDisk = filesOnDisk || catalogOnDisk || entry?.ships_with_runtime === true;
  return { files, hasDownloadable, isCatalog, isCatalogEntry, onDisk };
}
