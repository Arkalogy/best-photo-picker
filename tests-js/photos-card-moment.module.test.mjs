// @ts-check
import { beforeEach, describe, expect, test } from "vitest";
import { renderCardHTML } from "../bpp/web/static/js/modules/photos-card.mjs";

beforeEach(() => {
  /** @type {any} */ (globalThis.window).momentKeepers = new Set(["b.jpg"]);
});

const base = (extra) => ({ filename: "x.jpg", thumb_hash: "h", aggregate_score: 0.7, ...extra });

describe("renderCardHTML moment grouping classes", () => {
  test("photo in a multi-photo Moment gets in-moment + an a/b shade class", () => {
    const even = renderCardHTML(
      base({ filepath: "a.jpg", moment_cluster_id: 4, moment_size: 10 }),
      0
    );
    expect(even).toContain("in-moment");
    expect(even).toContain("moment-a"); // even moment id → shade A
    const odd = renderCardHTML(
      base({ filepath: "z.jpg", moment_cluster_id: 5, moment_size: 10 }),
      9
    );
    expect(odd).toContain("moment-b"); // odd moment id → shade B
  });
  test("the keeper photo also gets moment-keeper", () => {
    const html = renderCardHTML(
      base({ filepath: "b.jpg", moment_cluster_id: 4, moment_size: 10, aggregate_score: 0.9 }),
      1
    );
    expect(html).toContain("moment-keeper");
  });
  test("a singleton photo gets neither", () => {
    const html = renderCardHTML(
      base({ filepath: "c.jpg", moment_cluster_id: 0, moment_size: 1 }),
      2
    );
    expect(html).not.toContain("in-moment");
  });
});
