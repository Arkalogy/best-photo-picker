// @ts-check
import { describe, expect, test } from "vitest";

import {
  computeMomentKeepers,
  momentAccentHue,
  momentClasses,
  momentScore,
} from "../bpp/web/static/js/modules/moments-view.mjs";

describe("momentClasses", () => {
  test("singleton → empty string", () => {
    expect(momentClasses({ filepath: "a.jpg", moment_size: 1 })).toBe("");
  });
  test("even moment id → shade a, odd → shade b", () => {
    expect(momentClasses({ filepath: "a.jpg", moment_cluster_id: 4, moment_size: 3 })).toContain(
      "moment-a"
    );
    expect(momentClasses({ filepath: "b.jpg", moment_cluster_id: 5, moment_size: 3 })).toContain(
      "moment-b"
    );
  });
  test("keeper gets moment-keeper, others don't", () => {
    const keepers = new Set(["k.jpg"]);
    expect(
      momentClasses({ filepath: "k.jpg", moment_cluster_id: 2, moment_size: 3 }, keepers)
    ).toContain("moment-keeper");
    expect(
      momentClasses({ filepath: "o.jpg", moment_cluster_id: 2, moment_size: 3 }, keepers)
    ).not.toContain("moment-keeper");
  });
});

describe("computeMomentKeepers", () => {
  test("picks the sharpest/best-face shot per multi-photo Moment", () => {
    const photos = [
      { filepath: "a.jpg", moment_cluster_id: 1, moment_size: 2, blur_score: 0.4, face_score: 0.5 },
      { filepath: "b.jpg", moment_cluster_id: 1, moment_size: 2, blur_score: 0.9, face_score: 0.8 }, // keeper
      { filepath: "c.jpg", moment_cluster_id: 0, moment_size: 1, blur_score: 0.99 }, // singleton
    ];
    const keepers = computeMomentKeepers(photos);
    expect(keepers.has("b.jpg")).toBe(true);
    expect(keepers.has("a.jpg")).toBe(false);
    expect(keepers.size).toBe(1);
  });

  test("singletons and size<2 never produce a keeper", () => {
    const photos = [
      { filepath: "x.jpg", moment_cluster_id: 0, moment_size: 1, blur_score: 1 },
      { filepath: "y.jpg", moment_cluster_id: 5, moment_size: 1, blur_score: 1 },
    ];
    expect(computeMomentKeepers(photos).size).toBe(0);
  });

  test("one keeper per distinct Moment", () => {
    const photos = [
      { filepath: "a.jpg", moment_cluster_id: 1, moment_size: 2, blur_score: 0.9, face_score: 0.1 },
      { filepath: "b.jpg", moment_cluster_id: 1, moment_size: 2, blur_score: 0.2, face_score: 0.1 },
      { filepath: "c.jpg", moment_cluster_id: 2, moment_size: 2, blur_score: 0.3, face_score: 0.1 },
      { filepath: "d.jpg", moment_cluster_id: 2, moment_size: 2, blur_score: 0.8, face_score: 0.1 },
    ];
    const keepers = computeMomentKeepers(photos);
    expect([...keepers].sort()).toEqual(["a.jpg", "d.jpg"]);
  });

  test("empty / nullish input is safe", () => {
    expect(computeMomentKeepers([]).size).toBe(0);
    expect(computeMomentKeepers(/** @type {any} */ (undefined)).size).toBe(0);
  });
});

describe("momentScore", () => {
  test("faceless shots fall back to sharpness + overall", () => {
    const faceless = { blur_score: 0.8, aggregate_score: 0.5, face_score: 0 };
    expect(momentScore(faceless)).toBeCloseTo(0.8 * 0.7 + 0.5 * 0.3, 5);
  });
  test("with a face, sharpness + face quality drive it", () => {
    const withFace = { blur_score: 0.6, face_score: 0.9 };
    expect(momentScore(withFace)).toBeCloseTo(0.6 * 0.55 + 0.9 * 0.45, 5);
  });
});

describe("momentAccentHue", () => {
  test("deterministic and in [0,360)", () => {
    expect(momentAccentHue(1)).toBe(47);
    const h = momentAccentHue(123);
    expect(h).toBeGreaterThanOrEqual(0);
    expect(h).toBeLessThan(360);
    expect(momentAccentHue(1)).toBe(momentAccentHue(1));
  });
  test("adjacent moments get distinct accents", () => {
    expect(momentAccentHue(1)).not.toBe(momentAccentHue(2));
  });
});
