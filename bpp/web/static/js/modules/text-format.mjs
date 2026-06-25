// @ts-check
/**
 * Pure text / number formatting helpers.
 *
 * Loaded from index.html as:
 *   <script type="module">
 *     import { esc, escapeAttr, shortCount } from "/static/js/modules/text-format.mjs";
 *     window.esc = esc; window.escapeAttr = escapeAttr; window.shortCount = shortCount;
 *   </script>
 *
 * …so the rest of the runtime JS (still script-tag loaded) keeps working
 * unchanged. New code should `import` from this module directly.
 */

/**
 * Escape text for safe insertion into an HTML context.
 *
 * Uses the DOM to let the browser do the work — same behavior as
 * `element.textContent = s; element.innerHTML`.
 * @param {string} s
 * @returns {string}
 */
export function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/**
 * Escape text for safe insertion into an HTML attribute value.
 *
 * Must handle `&`, `'`, `"`, `<`, `>`. Order matters — `&` first so we
 * don't double-encode our own output. Accepts numbers / null /
 * undefined and coerces to string so call sites can wrap mixed-type
 * values uniformly (e.g. `escapeAttr(ar.value)` where `ar.value` is
 * `string | number`) without per-site type-check ceremony.
 *
 * @param {string | number | null | undefined} s
 * @returns {string}
 */
export function escapeAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/'/g, "&#39;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Escape text for a JS string literal inline in an HTML attribute
 * (e.g. `data-action="doThing" data-arg0="${escapeJsAttr(x)}"`). Still HTML-escapes
 * `"` and angle brackets, but also escapes backslashes and single
 * quotes.
 * @param {string} s
 * @returns {string}
 */
export function escapeJsAttr(s) {
  return s
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/"/g, "&quot;")
    .replace(/</g, "\\x3c")
    .replace(/>/g, "\\x3e");
}

/**
 * Human-friendly compact number formatter.
 *
 * Under 1 000 → plain integer.
 * 1 000+       → `1.5k`, trimming trailing `.0`.
 * @param {number} n
 * @returns {string}
 */
export function shortCount(n) {
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(n);
}
