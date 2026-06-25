// @ts-check
import { describe, expect, test } from "vitest";
import { reviewMetaLine, reviewMetaText } from "../bpp/web/static/js/modules/review-meta.mjs";

describe("reviewMetaLine — filename · timestamp · score under a review crop", () => {
  test("renders all three parts when present", () => {
    const html = reviewMetaLine({
      filename: "IMG_6346.HEIC",
      date: "2025-12-06T11:50:29",
      score: 0.8,
    });
    expect(html).toContain("IMG_6346.HEIC");
    expect(html).toContain("Dec 6, 2025");
    expect(html).toContain("11:50");
    expect(html).toContain("Score 80");
  });

  test("empty for null / empty meta (callers interpolate blindly)", () => {
    expect(reviewMetaLine(null)).toBe("");
    expect(reviewMetaLine(undefined)).toBe("");
    expect(reviewMetaLine({})).toBe("");
  });

  test("omits missing parts — filename only", () => {
    const html = reviewMetaLine({ filename: "a.jpg" });
    expect(html).toContain("a.jpg");
    expect(html).not.toContain("Score");
  });

  test("score 0 still renders (typeof number, not truthiness)", () => {
    expect(reviewMetaLine({ score: 0 })).toContain("Score 0");
  });

  test("escapes a filename with HTML metacharacters", () => {
    const html = reviewMetaLine({ filename: "<img onerror=x>.jpg" });
    expect(html).not.toContain("<img onerror");
    expect(html).toContain("&lt;img");
  });

  test("rounds score to a whole percent", () => {
    expect(reviewMetaLine({ score: 0.876 })).toContain("Score 88");
  });
});

describe("reviewMetaLine — layout: three stacked centered lines", () => {
  /** @param {string} html */
  function render(html) {
    const d = document.createElement("div");
    d.innerHTML = html;
    return d;
  }

  test("filename / timestamp / score are three separate blocks", () => {
    const d = render(
      reviewMetaLine({ filename: "IMG_6346.HEIC", date: "2025-12-06T11:50:29", score: 0.8 })
    );
    expect(d.querySelector(".review-meta-name")?.textContent).toBe("IMG_6346.HEIC");
    expect(d.querySelector(".review-meta-date")).toBeTruthy();
    expect(d.querySelector(".review-meta-score")?.textContent).toBe("Score 80");
  });

  test("no inline separator (stacked lines can't orphan a dot on wrap)", () => {
    const html = reviewMetaLine({ filename: "a.jpg", date: "2025-12-06T11:50:29", score: 0.8 });
    expect(html).not.toContain("review-meta-sep");
    expect(html).not.toContain("review-meta-sub");
  });

  test("omits a line entirely when its field is missing (no empty blocks)", () => {
    const d = render(reviewMetaLine({ date: "2025-12-06T11:50:29", score: 0.5 }));
    expect(d.querySelector(".review-meta-name")).toBeNull();
    expect(d.querySelector(".review-meta-date")).toBeTruthy();
    expect(d.querySelector(".review-meta-score")).toBeTruthy();

    const d2 = render(reviewMetaLine({ filename: "a.jpg" }));
    expect(d2.querySelector(".review-meta-name")).toBeTruthy();
    expect(d2.querySelector(".review-meta-date")).toBeNull();
    expect(d2.querySelector(".review-meta-score")).toBeNull();
  });
});

describe("reviewMetaText — plain-text tooltip variant", () => {
  test("joins parts with a middle dot", () => {
    expect(reviewMetaText({ filename: "a.jpg", date: "2025-12-06T11:50:29", score: 0.5 })).toBe(
      "a.jpg · Dec 6, 2025 · 11:50 AM · Score 50"
    );
  });

  test("empty for nothing", () => {
    expect(reviewMetaText(null)).toBe("");
    expect(reviewMetaText({})).toBe("");
  });
});
