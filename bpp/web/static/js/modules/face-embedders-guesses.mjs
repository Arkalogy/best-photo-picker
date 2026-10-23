// @ts-check
/**
 * The picker's menu when part of a row's status couldn't be loaded:
 * what's left out (anything acting on the missing part) and the way back
 * (load the list again). Split from face-embedders-rowstate.mjs.
 */

/**
 * Drop the menu items that would act on what the last load couldn't
 * fetch — each item names what it relies on in ``needs`` (its install
 * record, its license, which face model is in use) where it's built —
 * relabel the license item when it's unknown whether you accepted it (the
 * dialog then opens read-only: accepting again would add a duplicate to
 * the license record), and offer to load it again.
 *
 * @param {Array<object>} items
 * @param {{installUnknown: boolean, licenseUnknown: boolean, methodUnknown: boolean}} state
 * @returns {Array<object>}
 */
export function _withoutGuesses(items, state) {
  /** @type {Record<string, boolean>} */
  const missing = {
    install: state.installUnknown,
    license: state.licenseUnknown,
    method: state.methodUnknown,
  };
  if (!Object.values(missing).some(Boolean)) return items;
  const kept = items
    .filter((/** @type {any} */ item) => !(item.needs || []).some((/** @type {string} */ n) => missing[n]))
    .map((/** @type {any} */ item) =>
      state.licenseUnknown && item.action === "openFaceEmbedderAcceptance"
        ? { ...item, label: "View the model’s license", sub: "couldn’t check it’s accepted", done: false }
        : item,
    );
  kept.push({
    label: "Load the list again",
    sub: "part of this model’s status couldn’t be loaded",
    action: "loadFaceEmbedderPicker",
    args: [],
    enabled: true,
  });
  return kept;
}
