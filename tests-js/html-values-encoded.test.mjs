// @ts-check
/**
 * Every value the page writes into HTML goes through an encoder, unless
 * it is markup or a constant the app itself wrote.
 *
 * scripts/html-values.mjs traces each value placed in an HTML string
 * back to where it comes from. What it cannot prove safe is listed in
 * html-values-baseline.json: markup built elsewhere in the app, rows of
 * the app's own constant tables. A new entry means a new raw value in
 * the page: wrap it with esc() / escapeAttr() / escVal(), or, if it
 * really is app-made markup, add it to the baseline in the same change.
 */
import { readFileSync } from "node:fs";

import { expect, test } from "vitest";

import { reportedValues } from "../scripts/html-values.mjs";

const baseline = JSON.parse(readFileSync("tests-js/html-values-baseline.json", "utf8"));

/** @param {Record<string, Record<string, number>>} have @param {Record<string, Record<string, number>>} allowed */
function beyond(have, allowed) {
  /** @type {string[]} */
  const out = [];
  for (const [file, exprs] of Object.entries(have)) {
    for (const [expr, count] of Object.entries(exprs)) {
      const known = (allowed[file] || {})[expr] || 0;
      if (count > known)
        out.push(`${file}: ${expr}${known ? ` (${count} times, baseline ${known})` : ""}`);
    }
  }
  return out;
}

test("no value reaches page HTML without an encoder", () => {
  expect(
    beyond(reportedValues(), baseline),
    "raw values written into HTML; encode them (see this file's header)"
  ).toEqual([]);
});

test("the baseline lists nothing that is gone", () => {
  expect(
    beyond(baseline, reportedValues()),
    "remove these entries from tests-js/html-values-baseline.json (or lower their count)"
  ).toEqual([]);
});
