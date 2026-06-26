// @ts-check
/**
 * P8 — typed action registry that replaces the string-lookup-against-window
 * dispatcher in globals.js.
 *
 * Pre-P8 the click dispatcher in globals.js:233 did `fn = window[name]` for
 * every `data-action`. That contract has three problems:
 *
 *  1. Every handler has to live on `window` — ~890 globals, audited.
 *  2. There's no typing: a typo in `data-action="setOvverride"` silently
 *     no-ops with a console.warn, no compile-time signal.
 *  3. Plugins or modules that want to expose a handler must
 *     `window.X = X` somewhere, and the cleanup story is "you can't."
 *
 * The registry takes a named handler and stores it in a map. The dispatcher
 * (rewired in `dispatcher.mjs`) consults the registry FIRST, falling back to
 * `window[name]` for the ~245 unmigrated handlers during the deprecation
 * window. As each handler migrates to `registerAction`, its `window.X = X`
 * bridge can be dropped.
 *
 * Authoring contract:
 *
 *   import { registerAction } from "./action-registry.mjs";
 *
 *   function setOverride(event, photoId) { ... }
 *   registerAction("setOverride", setOverride);
 *
 * The handler signature matches the existing pre-P8 pattern:
 *   - If `data-pass-event` is on the element, `event` is the first arg.
 *   - `data-arg0`, `data-arg1`, ... become subsequent positional args.
 *   - `this` inside the handler is the clicked element.
 */

/** @type {Map<string, Function>} */
const _registry = new Map();

// The dispatcher in globals.js consults window.__bppActionRegistry on every
// click. We expose the registry's Map directly (not a wrapper) so the
// dispatcher's lookup stays one method call. Test code interacts via the
// named exports above; production lookup is via this window binding.
if (typeof window !== "undefined") {
  window.__bppActionRegistry = _registry;
}

/**
 * Register a handler for a `data-action="<name>"` site.
 *
 * @param {string} name — must match the `data-action` attribute exactly.
 *   Convention is camelCase, mirroring the pre-P8 window-global names.
 * @param {Function} handler — invoked with `this`=clicked element and the
 *   coerced args from `data-arg*` (and `event` first when
 *   `data-pass-event` is set).
 *
 * Re-registering the same name throws — most "duplicate registration"
 * cases are accidental (two modules claim the same action). The error
 * message names the previous registrar via the function's `name` property
 * when available.
 *
 * `replaceAction(name, handler)` is the explicit override hook for
 * test fixtures or plugins that intentionally take over a name.
 */
export function registerAction(name, handler) {
  if (_registry.has(name)) {
    const prev = _registry.get(name);
    const prevName = (prev && prev.name) || "<anonymous>";
    throw new Error(
      `action ${JSON.stringify(name)} already registered (existing: ${prevName}). ` +
        `Use replaceAction() to override intentionally.`,
    );
  }
  _registry.set(name, handler);
}

/**
 * Replace an existing registration. Test-only / plugin-override hook.
 * @param {string} name
 * @param {Function} handler
 */
export function replaceAction(name, handler) {
  _registry.set(name, handler);
}

/**
 * Look up a handler. Returns undefined when no handler is registered;
 * the dispatcher falls back to `window[name]` in that case.
 * @param {string} name
 * @returns {Function | undefined}
 */
export function lookupAction(name) {
  return _registry.get(name);
}

/**
 * Test-only: drop every registration. Production code should never call
 * this — the registry's lifetime is the page lifetime.
 */
export function _resetRegistryForTests() {
  _registry.clear();
}

/**
 * Test-only: enumerate registered action names. Used by the
 * window-pollution gate to know which handlers no longer need their
 * `window.X = X` bridge.
 * @returns {string[]}
 */
export function _registeredNames() {
  return Array.from(_registry.keys());
}
