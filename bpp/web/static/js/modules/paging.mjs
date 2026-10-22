// @ts-check
/**
 * Page through a `limit`/`offset` list endpoint.
 *
 * The server caps each page; a 20k-photo tag or a year of calendar
 * photos must not arrive as one response. Each page goes to `onPage`
 * as it lands (the grid shows the first page while the rest load) and
 * the walk stops as soon as `stillWanted()` says the user moved on.
 */

import { apiFetch } from "./api-client.mjs";

/**
 * @param {string} url - endpoint, with or without a query string
 * @param {{ pageSize?: number, key?: string, startOffset?: number,
 *           onPage?: (items: any[]) => void, stillWanted?: () => boolean }} [opts]
 * @returns {Promise<any[]>} every item, in order
 */
export async function fetchAllPages(url, opts = {}) {
  const pageSize = opts.pageSize || 1000;
  const key = opts.key || "photos";
  const sep = url.includes("?") ? "&" : "?";
  /** @type {any[]} */
  const all = [];
  let offset = opts.startOffset || 0;
  for (;;) {
    if (opts.stillWanted && !opts.stillWanted()) break;
    const data = await apiFetch(`${url}${sep}limit=${pageSize}&offset=${offset}`);
    // The user may have left this view while the page was in flight:
    // then it must not reach the (now reused) grid.
    if (opts.stillWanted && !opts.stillWanted()) break;
    const items = /** @type {any[]} */ (data?.[key] || []);
    all.push(...items);
    opts.onPage?.(items);
    offset += items.length;
    // Endpoints signal the end with has_more, or with total (the Trash /
    // Hidden listings); without either, one page is all there is.
    const more =
      data?.has_more ?? (typeof data?.total === "number" ? offset < data.total : false);
    if (!more || items.length === 0) break;
  }
  return all;
}
