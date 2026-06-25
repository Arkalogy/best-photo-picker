// @ts-check
/**
 * Pure score-to-display helpers.
 *
 * Score is a 0..1 quality estimate. These helpers map it onto:
 *   - a human label + color tokens (qualityLabel) for the lightbox
 *   - a CSS variable name (barColor) for inline progress bars
 *   - a translucent rgba background (scoreBadgeBg) for grid badges
 *
 * Bridged onto window via index.html's module bootstrap.
 */

/**
 * @typedef {{ text: string, fill: string, color: string }} QualityLabel
 */

/**
 * Map a 0..1 score to a quality label + CSS color tokens.
 *
 * Thresholds match the visual palette used in the lightbox info panel:
 *   ≥ 0.7  → Great  (green)
 *   ≥ 0.5  → Good   (accent / blue)
 *   ≥ 0.3  → Fair   (subtle gray)
 *   else   → Low    (red)
 *
 * @param {number} score - 0..1.
 * @returns {QualityLabel}
 */
export function qualityLabel(score) {
  if (score >= 0.7) {
    return { text: "Great", fill: "rgba(48,209,88,0.22)", color: "var(--green)" };
  }
  if (score >= 0.5) {
    return { text: "Good", fill: "rgba(10,132,255,0.22)", color: "var(--accent)" };
  }
  if (score >= 0.3) {
    return { text: "Fair", fill: "rgba(255,255,255,0.10)", color: "var(--text2)" };
  }
  return { text: "Low", fill: "rgba(255,69,58,0.22)", color: "var(--red)" };
}

/**
 * Map a 0..1 score to a CSS variable name for progress-bar fill color.
 *
 * Three-step palette: green / accent / red. The breakpoint at 0.4 (not
 * 0.5) is intentional — it gives "good enough" photos blue rather than
 * red, matching how the user thinks about the lower bound of useful.
 *
 * @param {number} s - 0..1.
 * @returns {string} A `var(--token)` CSS expression.
 */
export function barColor(s) {
  if (s >= 0.7) return "var(--green)";
  if (s >= 0.4) return "var(--accent)";
  return "var(--red)";
}

/**
 * Translucent rgba color for the score badge background on grid cards.
 *
 * Matches the same three-step palette as barColor but at a higher
 * alpha so the badge reads as a colored chip over the thumbnail.
 *
 * @param {number} s - 0..1.
 * @returns {string} An `rgba(r,g,b,a)` CSS color.
 */
export function scoreBadgeBg(s) {
  if (s >= 0.7) return "rgba(48,209,88,0.55)";
  if (s >= 0.4) return "rgba(10,132,255,0.50)";
  return "rgba(255,69,58,0.50)";
}
