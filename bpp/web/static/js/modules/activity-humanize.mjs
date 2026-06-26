// @ts-check
/**
 * Humanizer for the friendly "Recent Activity" bell-dropdown.
 *
 * The bell-dropdown is the *user-friendly* surface; the Settings → Activity
 * tab is the full *technical* log. This module is the only thing standing
 * between the two: it turns raw ring-buffer log lines into calm, plain
 * language — or hides them entirely.
 *
 * Design: ALLOWLIST, not denylist. A raw line is shown in the friendly feed
 * ONLY if a rule explicitly maps it to friendly text. Everything else is
 * dropped (it still lives in the Activity tab). That means new technical
 * logging added anywhere in the backend never leaks into the consumer-facing
 * feed by default — it has to be deliberately promoted with a rule here.
 *
 * The one exception is unmatched ERROR lines: a genuine unexpected error
 * shouldn't vanish from the friendly feed, so it falls through to a generic
 * "Something went wrong" note that points at the Activity tab for detail.
 */

/** Strip the standard `HH:MM:SS [LEVEL ] module.name:` log prefix. */
const PREFIX_RE = /^\d{2}:\d{2}:\d{2}\s+\[\w+\s*\]\s+[\w.]+:\s*/;

/** @param {string|number} n */
function fmtNum(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString("en-US") : String(n);
}

/**
 * @param {string|number} n
 * @param {string} word
 */
function plural(n, word) {
  return Number(n) === 1 ? word : word + "s";
}

/**
 * @typedef {Object} HumanizeRule
 * @property {RegExp} re - Matched against the prefix-stripped message body.
 * @property {boolean} [hide] - Drop the line from the friendly feed.
 * @property {string|((m: RegExpMatchArray) => string|null)} [text] - Friendly text (or null to drop).
 * @property {"INFO"|"WARNING"|"ERROR"} [level] - Override the displayed level.
 */

/**
 * Ordered rules — first match wins, so put specific patterns before
 * generic ones. Keep phrasing consumer-grade: no pids, no module names,
 * no "subprocess", no exit codes.
 *
 * @type {HumanizeRule[]}
 */
const RULES = [
  // ── Import ────────────────────────────────────────────────────────────
  { re: /^Import starting/i, text: "Importing photos…" },
  {
    re: /^Import complete: (\d+) imported/i,
    text: (m) => `Imported ${fmtNum(m[1])} ${plural(m[1], "photo")}`,
  },
  { re: /^Import scan/i, hide: true },
  { re: /^Import done in/i, hide: true },

  // ── Analysis ──────────────────────────────────────────────────────────
  { re: /^Analysis started/i, text: "Analyzing your photos…" },
  {
    re: /^Scoring subprocess done.*?(\d+) results from (\d+) images/i,
    text: (m) => `Analyzed ${fmtNum(m[1])} ${plural(m[1], "photo")}`,
  },
  {
    re: /^Flagged (\d+) new photo\(s\) as possibly sensitive \((\d+) total\)/i,
    text: (m) =>
      `Flagged ${fmtNum(m[1])} ${plural(m[1], "photo")} as possibly sensitive — review in the Sensitive album`,
  },
  { re: /^Starting scoring subprocess/i, hide: true },
  { re: /^Phase /i, hide: true },
  { re: /^Saved \d+ pet detections/i, hide: true },
  { re: /^Wrote \d+ photos to DB/i, hide: true },
  { re: /^CLIP Phase/i, hide: true },
  { re: /^CLIP model not available/i, hide: true },

  // ── Selection ─────────────────────────────────────────────────────────
  {
    re: /^Selection: (\d+)\/\d+ chosen/i,
    text: (m) => `Selected your ${fmtNum(m[1])} best ${plural(m[1], "photo")}`,
  },

  // ── Dedupe / similarity ───────────────────────────────────────────────
  {
    re: /^Near-duplicate clustering:.*?\((\d+) photos? have a near-duplicate/i,
    text: (m) =>
      Number(m[1]) > 0
        ? `Found ${fmtNum(m[1])} possible duplicate ${plural(m[1], "photo")}`
        : null,
  },
  { re: /^Semantic dedup/i, hide: true },
  { re: /^Dedup (pass|final)/i, hide: true },

  // ── Model downloads (capability) ──────────────────────────────────────
  // The "pet detection disabled" preflight line is the user-facing summary;
  // the lower-level "Failed to download YOLO…" duplicate is suppressed so we
  // don't show the same failure twice.
  { re: /pet detection disabled/i, level: "WARNING", text: "Pet detection isn't available on this device" },
  { re: /^Failed to download YOLO/i, hide: true },
  { re: /^Downloading YOLO\w* model/i, text: "Downloading pet-detection model…" },
  { re: /^CLIP model download failed/i, level: "WARNING", text: "Some photo features are unavailable" },
  { re: /^Downloading .* model/i, text: "Downloading AI model…" },

  // ── Startup / backfill / recovery noise (all hidden) ──────────────────
  { re: /backfill/i, hide: true },
  { re: /^Smart album/i, hide: true },
  { re: /^CLIP (vocabulary|text) initialised/i, hide: true },
  { re: /^Startup scan/i, hide: true },
  { re: /^Starting server at/i, hide: true },
  { re: /^Library:/i, hide: true },
  { re: /^Recovered \d+ interrupted renames/i, hide: true },
  { re: /^Recovery: /i, hide: true },
  { re: /^Update check failed/i, hide: true },
  { re: /^Face pipeline/i, hide: true },
  { re: /^Found pending clip_extraction/i, hide: true },
];

/**
 * @typedef {Object} RawEntry
 * @property {number} ts
 * @property {string} level
 * @property {string} [msg]
 */

/**
 * @typedef {Object} FriendlyEntry
 * @property {number} ts
 * @property {"INFO"|"WARNING"|"ERROR"|string} level
 * @property {string} text
 */

/**
 * Turn one raw ring-buffer entry into a friendly feed item, or null if it
 * should not appear in the friendly feed.
 *
 * @param {RawEntry|null|undefined} entry
 * @returns {FriendlyEntry|null}
 */
export function humanizeActivity(entry) {
  if (!entry) return null;
  const level = entry.level || "INFO";
  const raw = entry.msg || "";
  const body = raw.replace(PREFIX_RE, "");

  for (const rule of RULES) {
    const m = body.match(rule.re);
    if (!m) continue;
    if (rule.hide) return null;
    const text = typeof rule.text === "function" ? rule.text(m) : rule.text;
    if (!text) return null;
    return { ts: entry.ts, level: rule.level || level, text };
  }

  // Unmatched ERROR must not silently vanish from the friendly feed.
  if (level === "ERROR") {
    return {
      ts: entry.ts,
      level: "ERROR",
      text: "Something went wrong — open Activity for details.",
    };
  }

  // Unmatched INFO / WARNING is plumbing — Activity tab only.
  return null;
}

/**
 * Map a list of raw entries to friendly items, dropping the hidden ones.
 *
 * @param {RawEntry[]} entries
 * @returns {FriendlyEntry[]}
 */
export function humanizeActivityList(entries) {
  /** @type {FriendlyEntry[]} */
  const out = [];
  for (const e of entries) {
    const h = humanizeActivity(e);
    if (h) out.push(h);
  }
  return out;
}
