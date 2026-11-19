// Find values written into page HTML without an encoder.
//
// The page builds HTML as strings (template literals, "+" chains,
// accumulators assigned to innerHTML). Every value placed in such a
// string must be one of:
//
//   * markup the app built itself (another HTML string),
//   * a number or a boolean the code computed,
//   * the result of an encoder: esc(), escapeAttr(), escVal(), or one of
//     the URL builders (thumbUrl, faceCropUrl, petCropUrl).
//
// Anything else is reported: a field of an object, a parameter, the
// result of some other call. Such a value may be text from the opened
// library, whatever type the page expects it to be.
//
//   node scripts/html-values.mjs            list what is reported
//
// tests-js/html-values-encoded.test.mjs compares the list with
// tests-js/html-values-baseline.json (app-made markup the checker can't
// follow, listed by hand, with how often each is written).
//
// What it does not see: a value that reaches the page some other way
// than an HTML string (a DOM property, a library call it doesn't know),
// `.length` of something that isn't a string or an array, and what
// happens inside functions of other files beyond "returns markup".
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ModuleAnalysis } from "./html-values-module.mjs";

export const JS_ROOT = "bpp/web/static/js";

function listFiles(root) {
  const found = [];
  (function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(mjs|js)$/.test(entry.name)) found.push(full);
    }
  })(root);
  return found.sort();
}

/** Analyse every script under `root`: [{file, line, expr, at}]. */
export function analyzeAll(root = JS_ROOT) {
  const modules = new Map();
  const resolve = (fromFile, source) => {
    if (!source.startsWith(".")) return null;
    return modules.get(path.normalize(path.join(path.dirname(fromFile), source))) || null;
  };
  for (const file of listFiles(root)) {
    modules.set(
      path.normalize(file),
      new ModuleAnalysis(fs.readFileSync(file, "utf8"), file, resolve)
    );
  }
  const hits = [];
  for (const [file, module] of modules) {
    for (const hit of module.analyze()) hits.push({ file: file.split(path.sep).join("/"), ...hit });
  }
  return hits;
}

/**
 * Every reported value in the page's scripts, with how many times it is
 * written: {file: {expr: count}}.
 */
export function reportedValues(root = JS_ROOT) {
  const out = {};
  for (const hit of analyzeAll(root)) {
    const exprs = (out[hit.file] ||= {});
    exprs[hit.expr] = (exprs[hit.expr] || 0) + 1;
  }
  const sorted = (obj) =>
    Object.fromEntries(Object.entries(obj).sort(([x], [y]) => (x < y ? -1 : 1)));
  return sorted(
    Object.fromEntries(Object.entries(out).map(([file, exprs]) => [file, sorted(exprs)]))
  );
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const hits = analyzeAll();
  for (const hit of hits) {
    const via = hit.at.text === hit.expr ? "" : `   (through ${hit.at.text.slice(0, 60)})`;
    console.log(`${hit.file}:${hit.line}  ${hit.expr}${via}`);
  }
  console.log(`${hits.length} value(s) written into HTML without an encoder`);
}
