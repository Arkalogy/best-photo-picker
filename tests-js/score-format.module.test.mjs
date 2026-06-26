// @ts-check
// Module-style tests — counts towards v8 coverage.

import { describe, expect, test } from "vitest";

import {
  barColor,
  qualityLabel,
  scoreBadgeBg,
} from "../bpp/web/static/js/modules/score-format.mjs";

describe("qualityLabel", () => {
  test("≥ 0.7 → Great", () => {
    expect(qualityLabel(0.7).text).toBe("Great");
    expect(qualityLabel(0.95).text).toBe("Great");
    expect(qualityLabel(1.0).color).toBe("var(--green)");
  });

  test("0.5..0.7 → Good", () => {
    expect(qualityLabel(0.5).text).toBe("Good");
    expect(qualityLabel(0.69).text).toBe("Good");
    expect(qualityLabel(0.5).color).toBe("var(--accent)");
  });

  test("0.3..0.5 → Fair", () => {
    expect(qualityLabel(0.3).text).toBe("Fair");
    expect(qualityLabel(0.49).text).toBe("Fair");
    expect(qualityLabel(0.3).color).toBe("var(--text2)");
  });

  test("< 0.3 → Low", () => {
    expect(qualityLabel(0.0).text).toBe("Low");
    expect(qualityLabel(0.29).text).toBe("Low");
    expect(qualityLabel(0).color).toBe("var(--red)");
  });

  test("each tier returns a translucent rgba fill", () => {
    expect(qualityLabel(0.8).fill).toMatch(/^rgba\(/);
    expect(qualityLabel(0.6).fill).toMatch(/^rgba\(/);
    expect(qualityLabel(0.4).fill).toMatch(/^rgba\(/);
    expect(qualityLabel(0.1).fill).toMatch(/^rgba\(/);
  });
});

describe("barColor", () => {
  test("breakpoints 0.4 / 0.7", () => {
    expect(barColor(0.7)).toBe("var(--green)");
    expect(barColor(0.5)).toBe("var(--accent)");
    expect(barColor(0.4)).toBe("var(--accent)");
    expect(barColor(0.39)).toBe("var(--red)");
    expect(barColor(0)).toBe("var(--red)");
  });
});

describe("scoreBadgeBg", () => {
  test("returns rgba strings keyed by the same 0.4 / 0.7 thresholds", () => {
    expect(scoreBadgeBg(0.7)).toMatch(/^rgba\(48,209,88/);
    expect(scoreBadgeBg(0.4)).toMatch(/^rgba\(10,132,255/);
    expect(scoreBadgeBg(0)).toMatch(/^rgba\(255,69,58/);
  });
});
