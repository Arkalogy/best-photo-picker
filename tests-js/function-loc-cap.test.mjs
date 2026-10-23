// @ts-check
// 150-LOC cap on page-module functions — the JS twin of
// tests/test_function_loc_cap.py (which covers the Python package).
//
// A function past 150 lines is several steps that lost their names.
// Any function NOT in BASELINE must be <= 150 lines. BASELINE
// functions (over cap when the gate landed, 2026-10-18) are ratcheted:
// they may never GROW past their recorded size, and the moment one is
// split under the cap it must be REMOVED from the list. When this
// fails because a function grew, split it; do not raise a number.
import fs from "node:fs";
import path from "node:path";

import { describe, expect, test } from "vitest";

import { functionLengths } from "../scripts/function-loc.mjs";

const CAP = 150;
const ROOT = path.resolve(__dirname, "..");
const JS_ROOT = path.join(ROOT, "bpp/web/static/js");

/** file -> function -> size when measured. Shrink-only; never raise one. */
const BASELINE = new Map([]);

/** @returns {string[]} every page module, vendor excluded, repo-relative */
function sourceFiles() {
  /** @type {string[]} */
  const out = [];
  const walk = (/** @type {string} */ dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "vendor") walk(full);
      } else if (/\.(mjs|js)$/.test(entry.name)) {
        out.push(path.relative(ROOT, full));
      }
    }
  };
  walk(JS_ROOT);
  return out.sort();
}

const hits = functionLengths(
  sourceFiles().map((f) => path.join(ROOT, f)),
  0
).map((h) => ({
  ...h,
  file: path.relative(ROOT, h.file),
}));

describe("page-module functions stay under the 150-LOC cap", () => {
  test("no function outside the baseline is over cap", () => {
    const over = hits.filter((h) => h.loc > CAP && !BASELINE.has(`${h.file}:${h.name}`));
    expect(
      over.map((h) => `${h.file}:${h.line} ${h.name}() ${h.loc} LOC`),
      "Functions over the 150-LOC cap (split them; do not add to BASELINE)"
    ).toEqual([]);
  });

  test("a baseline function never grows, and leaves the list once under cap", () => {
    // A callback inside a function inherits its name: the largest is the outer one.
    /** @type {Map<string, number>} */
    const sizes = new Map();
    for (const h of hits) {
      const key = `${h.file}:${h.name}`;
      sizes.set(key, Math.max(h.loc, sizes.get(key) ?? 0));
    }
    const problems = [];
    for (const [key, recorded] of BASELINE) {
      const loc = sizes.get(key);
      if (loc === undefined) problems.push(`${key}: gone or renamed; drop it from BASELINE`);
      else if (loc > recorded) problems.push(`${key}: grew ${recorded} -> ${loc} LOC`);
      else if (loc <= CAP) problems.push(`${key}: now ${loc} LOC; remove it from BASELINE`);
    }
    expect(problems, "BASELINE drift").toEqual([]);
  });
});
