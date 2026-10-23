// @ts-check
/**
 * One helper builds every pet crop URL, encoding both parts, next to the
 * face one; page modules no longer assemble the path by hand.
 */
import { describe, expect, test } from "vitest";
import { readdirSync, readFileSync } from "node:fs";

import { petCropUrl } from "../bpp/web/static/js/modules/api-client.mjs";

describe("petCropUrl", () => {
  test("encodes the hash and index", () => {
    expect(petCropUrl("a b", 3)).toContain("/api/v1/pets/crop/a%20b/3");
  });

  test("no page module builds the pet crop path itself", () => {
    const dir = "bpp/web/static/js/modules";
    const offenders = readdirSync(dir)
      .filter((f) => f.endsWith(".mjs") && f !== "api-client.mjs")
      .filter((f) => readFileSync(`${dir}/${f}`, "utf8").includes("/api/v1/pets/crop/"));
    expect(offenders).toEqual([]);
  });
});
