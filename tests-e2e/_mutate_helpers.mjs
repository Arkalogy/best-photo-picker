// @ts-check
/**
 * Helpers for state-mutating e2e tests. Every helper has a matching
 * cleanup so the user's library returns to its original state after
 * the test. ALWAYS pair `setOverride(filepath, "include")` with
 * `setOverride(filepath, null)` in a try/finally.
 *
 * SAFETY: every mutating helper calls `assertE2EFixture(page)` before
 * doing anything destructive. The server publishes
 * `/api/v1/_diag/is_e2e_fixture` based on a sentinel file the e2e
 * setup script drops in the library directory. A real user library
 * has no sentinel, so a mis-pointed test suite fails fast with a
 * clear error instead of silently polluting the library (which is
 * how `__e2e_album_*` rows leaked into the demo library before this
 * guard existed).
 */

import { authToken } from "./_helpers.mjs";

/**
 * Cached per page so we don't hit the endpoint on every helper call.
 * @type {WeakMap<object, Promise<boolean>>}
 */
const _fixtureCheckCache = new WeakMap();

/**
 * @param {import("@playwright/test").Page} page
 */
async function _isE2EFixture(page) {
  let cached = _fixtureCheckCache.get(page);
  if (!cached) {
    cached = (async () => {
      const token = await authToken(page);
      const r = await page.request.get(`/api/v1/_diag/is_e2e_fixture?_token=${token}`);
      if (!r.ok()) return false;
      const body = await r.json();
      return body && body.is_fixture === true;
    })();
    _fixtureCheckCache.set(page, cached);
  }
  return cached;
}

/**
 * Throw a loud, clear error if the test suite is pointed at a non-
 * fixture library. Called from every mutating helper. The error
 * message is the load-bearing piece — operators see it and know
 * exactly what to do.
 *
 * @param {import("@playwright/test").Page} page
 */
export async function assertE2EFixture(page) {
  if (await _isE2EFixture(page)) return;
  throw new Error(
    "REFUSING TO MUTATE: server is NOT pointed at an e2e fixture library.\n" +
      "The mutating e2e helpers (album/tag/override/hide create) will only run " +
      "against a library that has the `.bpp-e2e-fixture` sentinel file at its " +
      "root. Run `python scripts/setup_e2e_library.py` to provision one, then " +
      "restart the server with `--library /tmp/bpp_e2e_library` before re-running " +
      "the suite."
  );
}

/**
 * @param {import("@playwright/test").Page} page
 * @param {string} path
 * @param {Record<string, any>} body
 */
export async function postApi(page, path, body) {
  // Skip the fixture check for the fixture-check call itself to avoid
  // recursion. Any other mutating POST must clear the guard first.
  const postPath = new URL(path, "http://bpp.local").pathname;
  if (postPath !== "/api/v1/_diag/is_e2e_fixture") {
    await assertE2EFixture(page);
  }
  const token = await authToken(page);
  const sep = path.includes("?") ? "&" : "?";
  const resp = await page.request.post(`${path}${sep}_token=${token}`, {
    data: body,
    headers: { "Content-Type": "application/json" },
  });
  return resp;
}

/**
 * @param {import("@playwright/test").Page} page
 * @param {string} path
 */
export async function deleteApi(page, path) {
  await assertE2EFixture(page);
  const token = await authToken(page);
  const sep = path.includes("?") ? "&" : "?";
  return page.request.delete(`${path}${sep}_token=${token}`);
}

/**
 * Force toggle the favorite state to a target. Returns a callback that
 * reverts to the original state — call from finally.
 *
 * @param {import("@playwright/test").Page} page
 * @param {string} filepath
 */
export async function favoriteAndReturnRestorer(page, filepath) {
  await assertE2EFixture(page);
  // /api/favorite is a toggle. Call once to flip; remember and call
  // again at cleanup time to flip back. We don't read the original
  // state — the toggle pattern is symmetric.
  const r1 = await postApi(page, "/api/v1/favorite", { filepath });
  if (!r1.ok()) throw new Error(`favorite POST failed: ${r1.status()}`);
  return async () => {
    await postApi(page, "/api/v1/favorite", { filepath });
  };
}

/**
 * Set an override and return a restorer that clears it back to whatever
 * it was originally (null or the prior mode).
 *
 * @param {import("@playwright/test").Page} page
 * @param {string} filepath
 * @param {"include" | "exclude" | null} originalMode
 * @param {"include" | "exclude" | null} newMode
 */
export async function setOverrideAndReturnRestorer(page, filepath, originalMode, newMode) {
  await assertE2EFixture(page);
  const r = await postApi(page, "/api/v1/override", { filepath, mode: newMode });
  if (!r.ok()) throw new Error(`override POST failed: ${r.status()}`);
  return async () => {
    await postApi(page, "/api/v1/override", { filepath, mode: originalMode });
  };
}

/**
 * Hide a photo. Returns a restorer that unhides.
 *
 * @param {import("@playwright/test").Page} page
 * @param {string} filepath
 */
export async function hideAndReturnRestorer(page, filepath) {
  await assertE2EFixture(page);
  const r = await postApi(page, "/api/v1/photos/hide", { filepaths: [filepath] });
  if (!r.ok()) throw new Error(`hide POST failed: ${r.status()}`);
  return async () => {
    await postApi(page, "/api/v1/photos/unhide", { filepaths: [filepath] });
  };
}

/**
 * Create a uniquely-named tag, attach it to a photo, and return a
 * restorer that deletes the tag (which also removes it from the photo
 * via cascade or via remove-photo-tag first).
 *
 * @param {import("@playwright/test").Page} page
 * @param {number} photoId
 */
export async function tagPhotoAndReturnRestorer(page, photoId) {
  await assertE2EFixture(page);
  const tagName = `__e2e_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const r = await postApi(page, `/api/v1/photos/${photoId}/tags`, { name: tagName });
  if (!r.ok()) throw new Error(`add tag POST failed: ${r.status()}`);
  const body = await r.json();
  const tagId = body.tag_id;
  return {
    tagName,
    tagId,
    restore: async () => {
      // Remove from photo first (idempotent), then delete the tag entirely
      await deleteApi(page, `/api/v1/photos/${photoId}/tags/${tagId}`);
      await deleteApi(page, `/api/v1/tags/${tagId}`);
    },
  };
}

/**
 * Create a manual album and add a photo to it. Restorer deletes the
 * album, which also removes the photo association.
 *
 * @param {import("@playwright/test").Page} page
 * @param {string} filepath
 */
export async function manualAlbumWithPhotoAndReturnRestorer(page, filepath) {
  await assertE2EFixture(page);
  const name = `__e2e_album_${Date.now()}`;
  const r = await postApi(page, "/api/v1/albums", { name });
  if (!r.ok()) throw new Error(`create album POST failed: ${r.status()}`);
  const body = await r.json();
  const albumId = body.id;
  const r2 = await postApi(page, `/api/v1/albums/${albumId}/add-photos`, {
    filepaths: [filepath],
  });
  if (!r2.ok()) throw new Error(`add-photos POST failed: ${r2.status()}`);
  return {
    albumId,
    name,
    restore: async () => {
      await deleteApi(page, `/api/v1/albums/${albumId}`);
    },
  };
}
