// @ts-check
/**
 * Single source of truth for the use-context radio-card content.
 *
 * The use-context selector appears in two places:
 *
 *   1. Onboarding wizard, step 3 (``_stepUseContext`` in
 *      ``onboarding.mjs``).
 *   2. Settings → Models picker header (``_useContextControl`` in
 *      ``modals-face-embedders.mjs``).
 *
 * The project conventions call out that the wording on each card MUST stay
 * identical across both surfaces — a user re-reading their own
 * declaration in Settings should see exactly the same copy they
 * saw when they made the choice in onboarding. Inlining the array
 * in both files invites silent drift (already observed: "Most
 * people." appeared in one and not the other). This module pins
 * the canonical copy; both renderers import from here.
 *
 * If you need to change the wording, change it ONCE here. Both
 * surfaces update automatically, and the parity test in
 * ``tests-js/use-context-options.module.test.mjs`` guards against
 * future re-inlining.
 */

/**
 * @typedef {Object} UseContextOption
 * @property {"personal" | "research" | "commercial"} value
 * @property {string} title
 * @property {string} desc
 */

/** @type {ReadonlyArray<UseContextOption>} */
export const USE_CONTEXT_OPTIONS = Object.freeze([
  Object.freeze({
    value: "personal",
    title: "Personal",
    desc: "Curating my own photos. Most people.",
  }),
  Object.freeze({
    value: "research",
    title: "Research",
    desc: "Academic or non-commercial research work.",
  }),
  Object.freeze({
    value: "commercial",
    title: "Commercial",
    desc:
      "Paid work, client work, business. Restricted-license models " +
      "will be hard-blocked unless you assert separate rights.",
  }),
]);
