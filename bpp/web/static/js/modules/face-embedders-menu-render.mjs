// @ts-check
/**
 * Render one ⋯-menu item of the model picker from a descriptor
 * ``_buildMenuFromState`` produced (license, download, use-this-model,
 * run switch, uninstall). Split out of
 * ``face-embedders-rowstate.mjs`` for the 500-LOC cap.
 */

import { escapeAttr, esc } from "./text-format.mjs";

/**
 * Render one ⋯-menu item from a descriptor produced by
 * ``_buildActionMenu``. Disabled items carry no ``data-action`` (so a
 * click is a true no-op) and a muted sub-line explaining why they're
 * unavailable; completed items get a ✓.
 *
 * @param {object} item
 * @returns {string}
 */
export function _renderMenuItem(item) {
  const cls = [
    "fe-menu-item",
    item.danger && item.enabled ? "fe-menu-danger" : "",
    item.done ? "fe-menu-done" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const check = item.done ? ' <span class="fe-menu-check">✓</span>' : "";
  const labelHtml = `<span class="fe-menu-label">${esc(item.label)}${check}</span>`;
  const subHtml = item.sub
    ? `<span class="fe-menu-sub">${esc(item.sub)}</span>`
    : "";
  if (!item.enabled) {
    return `<button class="${cls}" disabled>${labelHtml}${subHtml}</button>`;
  }
  const args = (item.args || [])
    .map((a, i) => `data-arg${i}="${escapeAttr(String(a))}"`)
    .join(" ");
  return `<button class="${cls}" data-action="${escapeAttr(item.action)}" ${args}>${labelHtml}${subHtml}</button>`;
}
