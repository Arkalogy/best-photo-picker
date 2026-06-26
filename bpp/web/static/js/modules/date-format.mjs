// @ts-check
/**
 * Date / time formatting helpers.
 *
 * Loaded in index.html via the modules-bridge `<script type="module">`
 * which re-exposes these as globals on `window` so the existing
 * non-module callers (lightbox.js, calendar.js, memories.js, etc.)
 * keep working unchanged. New code should `import` from here.
 */

/** @type {readonly string[]} */
export const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** @type {readonly string[]} */
export const MONTHS_FULL = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/**
 * Format an ISO-like date string to a human-readable form.
 *
 * Returns "" for falsy input. Returns the original string for
 * unparseable input. Otherwise:
 *   default        → "Jun 15, 2024"
 *   style="time"   → "Jun 15, 2024 · 3:45 PM"   (or "· 3 PM" on the hour)
 *   style="relative" → "just now" / "5m ago" / "2h ago" / "3d ago" /
 *                       "Jun 15" (this year) / "Jun 15, 2024" (older)
 *
 * @param {string | Date | null | undefined} dateStr
 * @param {"time" | "relative" | undefined} [style]
 * @returns {string}
 */
export function formatDate(dateStr, style) {
  if (!dateStr) return "";
  const d = new Date(typeof dateStr === "string" ? dateStr.replace("T", " ") : dateStr);
  if (isNaN(/** @type {any} */ (d))) return String(dateStr);
  const y = d.getFullYear();
  const mo = d.getMonth();
  const day = d.getDate();
  if (style === "time") {
    const h = d.getHours();
    const m = d.getMinutes();
    const ampm = h >= 12 ? "PM" : "AM";
    const h12 = h % 12 || 12;
    const time =
      m > 0
        ? ` · ${h12}:${String(m).padStart(2, "0")} ${ampm}`
        : ` · ${h12} ${ampm}`;
    return `${MONTHS_SHORT[mo]} ${day}, ${y}${time}`;
  }
  if (style === "relative") {
    const diff = Date.now() - d.getTime();
    if (diff < 60000) return "just now";
    if (diff < 3600000) return Math.floor(diff / 60000) + "m ago";
    if (diff < 86400000) return Math.floor(diff / 3600000) + "h ago";
    if (diff < 604800000) return Math.floor(diff / 86400000) + "d ago";
    if (y === new Date().getFullYear()) return `${MONTHS_SHORT[mo]} ${day}`;
    return `${MONTHS_SHORT[mo]} ${day}, ${y}`;
  }
  return `${MONTHS_SHORT[mo]} ${day}, ${y}`;
}

/**
 * Convenience wrapper around `formatDate` with no style — used in
 * places that want a plain "Jun 15, 2024" stamp.
 *
 * @param {string | Date | null | undefined} dateStr
 * @returns {string}
 */
export function formatDateStamp(dateStr) {
  return formatDate(dateStr);
}
