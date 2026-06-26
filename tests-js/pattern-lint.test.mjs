// @ts-check
/**
 * Pattern-lint meta-tests for the JS modules. Each rule codifies a
 * project invariant that audits keep surfacing. Failures point at
 * the exact `file:line` plus the helper to use instead.
 *
 * The patterns enforced here came directly out of two repeated full
 * audits. ESLint doesn't have hooks for project-specific contextual
 * rules (e.g. "esc() is wrong INSIDE `title=`"), so we read each
 * source file once and apply targeted regex checks. Fast enough
 * (under 30ms on the current tree) to run on every CI invocation.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, test } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..");
const JS_MODULES_DIR = path.join(REPO_ROOT, "bpp", "web", "static", "js", "modules");

/** Read every `.mjs` file under JS_MODULES_DIR. Returns [{ path, src }]. */
async function jsModules() {
  const entries = await fs.readdir(JS_MODULES_DIR, { withFileTypes: true });
  const out = [];
  for (const e of entries) {
    if (!e.isFile() || !e.name.endsWith(".mjs")) continue;
    const full = path.join(JS_MODULES_DIR, e.name);
    out.push({ path: full, src: await fs.readFile(full, "utf-8") });
  }
  return out;
}

/**
 * Strip `// ...`, `/* ... *\/`, and string literals (template /
 * double / single) from JS source. Used to filter docstring /
 * comment mentions of the patterns we're hunting.
 *
 * Not a full parser — handles the cases that appear in our codebase
 * (template literals with nested ${} get partial blanking but the
 * non-string portions survive intact). Good enough for pattern lint.
 */
function stripStringsAndComments(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];
    // Line comment
    if (c === "/" && c2 === "/") {
      while (i < n && src[i] !== "\n") {
        out += " ";
        i++;
      }
      continue;
    }
    // Block comment
    if (c === "/" && c2 === "*") {
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) {
        out += src[i] === "\n" ? "\n" : " ";
        i++;
      }
      if (i < n) {
        out += "  "; // for the closing */
        i += 2;
      }
      continue;
    }
    // String literal — double / single / backtick. Walk to the close
    // (skipping escapes). Inside backticks we DON'T parse ${} bodies
    // as code; the body's regex hits are blanked too. That's overly
    // strict for some checks but safe (no false-pass).
    if (c === '"' || c === "'" || c === "`") {
      const quote = c;
      out += quote;
      i++;
      while (i < n && src[i] !== quote) {
        if (src[i] === "\\" && i + 1 < n) {
          out += "  ";
          i += 2;
          continue;
        }
        out += src[i] === "\n" ? "\n" : " ";
        i++;
      }
      if (i < n) {
        out += quote;
        i++;
      }
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

/**
 * Return [{ line, snippet }] for every line of `src` matching `pattern`.
 *
 * @param {string} src - source text to scan
 * @param {RegExp} pattern - regex applied per-line (against the
 *   strings-and-comments-stripped form)
 * @param {(srcLine: string, scrubLine: string) => boolean} [predicate]
 *   - optional secondary filter; receives the original source line
 *   plus the scrubbed line. Return false to skip a match.
 */
function findOffenders(src, pattern, predicate) {
  const scrubbed = stripStringsAndComments(src);
  const scrubLines = scrubbed.split("\n");
  const srcLines = src.split("\n");
  /** @type {{line: number, snippet: string}[]} */
  const out = [];
  for (let i = 0; i < scrubLines.length; i++) {
    if (!pattern.test(scrubLines[i])) continue;
    if (predicate && !predicate(srcLines[i], scrubLines[i])) continue;
    out.push({ line: i + 1, snippet: srcLines[i].trim() });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Rule 1: no raw `-1` / `-2` cluster sentinels in JS
// ---------------------------------------------------------------------------
//
// Should use CLUSTER_UNASSIGNED / CLUSTER_DISMISSED from constants.mjs.
// The pattern targets common comparison / assignment shapes near
// `cluster_id`:
//   cluster_id === -1
//   cluster_id !== -2
//   ?? -1
//   ?? -2
// and lateral phrasings.

describe("pattern-lint: cluster sentinels", () => {
  test("no raw -1 / -2 cluster sentinels in JS modules", async () => {
    const modules = await jsModules();
    // constants.mjs defines the values — skip it.
    const skip = new Set(["constants.mjs"]);
    const bad =
      /(cluster[_\s]*[Ii]d\s*[!=]=+\s*-[12]\b|cluster[_\s]*[Ii]d\s*\?\?\s*-[12]\b|\?\?\s*-[12]\b\s*(?:\)|;|,))/;
    const offenders = [];
    for (const m of modules) {
      if (skip.has(path.basename(m.path))) continue;
      for (const o of findOffenders(m.src, bad)) {
        offenders.push(`${path.relative(REPO_ROOT, m.path)}:${o.line}: ${o.snippet}`);
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        "Raw cluster sentinel found. Import CLUSTER_UNASSIGNED / " +
          "CLUSTER_DISMISSED from constants.mjs.\nOffenders:\n  " +
          offenders.join("\n  ")
      );
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 2: no esc() inside HTML attribute contexts
// ---------------------------------------------------------------------------
//
// Inside a template literal, `title="...${esc(x)}..."` is wrong
// because esc() doesn't escape double quotes. Use escapeAttr() for
// attribute interpolations. Text content (between tags) is fine with
// esc().
//
// We detect: any attribute-like prefix `(title|data-...|alt|placeholder|aria-...)="`
// immediately before a `${esc(`.

describe("pattern-lint: HTML attribute escaping", () => {
  test("no esc() inside title/alt/data-/placeholder/aria- attributes", async () => {
    const modules = await jsModules();
    // The stripped source removes string contents, so we can't scan
    // it for THIS rule — the patterns we want to catch live inside
    // template literals. Scan the raw source.
    const bad = /(title|alt|placeholder|data-[a-z0-9-]+|aria-[a-z0-9-]+)\s*=\s*"[^"]*\$\{esc\(/;
    const offenders = [];
    for (const m of modules) {
      const lines = m.src.split("\n");
      for (let i = 0; i < lines.length; i++) {
        if (!bad.test(lines[i])) continue;
        offenders.push(`${path.relative(REPO_ROOT, m.path)}:${i + 1}: ${lines[i].trim()}`);
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        "esc() inside an HTML attribute. esc() doesn't escape double " +
          'quotes — a value containing `"` will break out of the ' +
          "attribute. Use escapeAttr() instead.\nOffenders:\n  " +
          offenders.join("\n  ")
      );
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 3: no browser confirm() / alert() / prompt()
// ---------------------------------------------------------------------------
//
// Project rule: use appConfirm() / appPrompt() / toast() from
// dialogs.mjs + toast.mjs. The native dialogs block the runtime,
// have no styling control, and break the Tauri sidecar in some
// builds.

// ---------------------------------------------------------------------------
// Rule 4: no bare "…failed…" toasts — use toastError(action, err)
// ---------------------------------------------------------------------------
//
// Project error-toast policy: a user-facing error toast must name the
// ACTION, the REASON, and a RECOVERY hint. `toastError(action, err)`
// formats all three; a hand-rolled `toast("Failed to X: " + e.message)`
// (or `toast(\`X failed: ${err}\`)`) names no recovery and drifts in
// style. The 2026-06-12 review found ~35 of these across 12 files —
// this rule keeps the count at zero.

describe("pattern-lint: error-toast policy", () => {
  test("no toast('…failed…') — use toastError(action, err)", async () => {
    const modules = await jsModules();
    // toast.mjs defines the helpers; skip it.
    const skip = new Set(["toast.mjs"]);
    // Raw-source scan (the offending text lives inside string literals).
    const bad = /\btoast\(\s*["'`][^"'`]*[Ff]ail/;
    const offenders = [];
    for (const m of modules) {
      if (skip.has(path.basename(m.path))) continue;
      const lines = m.src.split("\n");
      for (let i = 0; i < lines.length; i++) {
        if (!bad.test(lines[i])) continue;
        // Explicit opt-out for legitimate summary toasts (e.g. "Renamed
        // 5 files, 2 failed" with counts, or a toast that already names
        // action + reason + recovery).
        if (lines[i].includes("toast-ok")) continue;
        offenders.push(`${path.relative(REPO_ROOT, m.path)}:${i + 1}: ${lines[i].trim()}`);
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        "Bare failure toast found. Use toastError(action, err) from " +
          "toast.mjs — it formats action + reason + recovery hint. " +
          "Genuine summary toasts may opt out with /* toast-ok: reason */.\n" +
          "Offenders:\n  " +
          offenders.join("\n  ")
      );
    }
  });
});

describe("pattern-lint: native dialogs", () => {
  test("no window.confirm / alert / prompt calls", async () => {
    const modules = await jsModules();
    // Match the bare call form. The stripped-source filter removes
    // strings + comments so docstring mentions ("calls confirm(...)")
    // are ignored. `window.confirm(` is also matched.
    const bad = /\b(?:window\.)?(?:confirm|alert|prompt)\s*\(/;
    const offenders = [];
    for (const m of modules) {
      for (const o of findOffenders(m.src, bad, (line) => {
        // Exclude function definitions / property bindings whose
        // NAME happens to contain confirm/alert/prompt (e.g.
        // `appConfirm(`, `_confirmResolve = ...`, `appPrompt(`).
        // The negative-lookbehind in regex is awkward to write
        // cross-engine; use a predicate instead.
        if (/[A-Za-z0-9_]\s*(?:confirm|alert|prompt)\s*\(/.test(line)) {
          // The call is preceded by an identifier char → it's a
          // method or namespaced call (e.g. appConfirm). Allow.
          return false;
        }
        return true;
      })) {
        offenders.push(`${path.relative(REPO_ROOT, m.path)}:${o.line}: ${o.snippet}`);
      }
    }
    if (offenders.length > 0) {
      throw new Error(
        "Browser confirm/alert/prompt found. Use appConfirm() / " +
          "appPrompt() / toast() from dialogs.mjs.\nOffenders:\n  " +
          offenders.join("\n  ")
      );
    }
  });
});

// ---------------------------------------------------------------------------
// Rule 6: a mutating apiFetch handler must surface failures via toastError
// ---------------------------------------------------------------------------
//
// Project error-toast policy: "Every mutating action handler (apiFetch
// POST/PUT/DELETE) MUST have a try/catch that calls toastError." apiFetch
// THROWS on non-2xx (api-client.mjs) — so a handler that does a mutating
// call but never calls toastError lets an HTTP failure fall through to the
// global boundary's nameless "Something went wrong" toast.
//
// The 2026-06-12 audit found ~50 such handlers; the 2026-06-16 ratchet was
// added to stop the count regrowing; the 2026-06-17 sweep converted 48 of
// them. This is a RATCHET: any NEW violation fails, and a
// baseline entry that's been fixed must be removed (the list can only
// shrink). The two entries left are NOT debt — they're intentional service
// WRAPPERS that deliberately let apiFetch reject so their caller toasts
// once (toasting in both would double-toast). Each is documented at its
// call site.

/** Extract top-level `function name(...) { ... }` bodies via brace match. */
function namedFunctionBodies(src) {
  const out = [];
  const re = /(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\([^)]*\)\s*\{/g;
  let m;
  while ((m = re.exec(src))) {
    const name = m[1];
    let i = re.lastIndex - 1;
    let depth = 0;
    const start = i;
    for (; i < src.length; i++) {
      const c = src[i];
      if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth === 0) {
          i++;
          break;
        }
      }
    }
    out.push({ name, body: src.slice(start, i) });
  }
  return out;
}

// file:func handlers with a mutating apiFetch and no toastError. ONLY
// removals allowed — when you wrap one in try/catch + toastError, delete
// its line here. Never add.
const ERROR_TOAST_BASELINE = new Set([
  // Service wrappers, NOT leaf handlers — each lets apiFetch reject so its
  // caller toasts exactly once (toasting here too would double-toast).
  // Documented at each call site. ONLY removals allowed; never add.
  "utils.mjs:_streamExport", // caller: doExport
  "sensitive.mjs:postSensitiveOverride", // caller: lbToggleSensitive
]);

describe("pattern-lint: error-toast on mutating apiFetch handlers", () => {
  test("a mutating apiFetch handler calls toastError (ratcheting baseline)", async () => {
    const modules = await jsModules();
    // method may sit a few lines below the apiFetch( call.
    const mutating = /apiFetch\s*\([\s\S]{0,400}?method\s*:\s*["'](?:POST|PUT|DELETE|PATCH)["']/;
    const found = new Set();
    for (const m of modules) {
      const file = path.basename(m.path);
      // Strip comments but KEEP string literals — the mutating-method
      // check matches `method: "POST"`, whose value is a string literal
      // (stripStringsAndComments would blank it and the rule would miss
      // every handler). Comment removal still drops docstring mentions.
      const src = m.src.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const fn of namedFunctionBodies(src)) {
        if (mutating.test(fn.body) && !/toastError\s*\(/.test(fn.body)) {
          found.add(`${file}:${fn.name}`);
        }
      }
    }
    const newViolations = [...found].filter((k) => !ERROR_TOAST_BASELINE.has(k)).sort();
    const fixed = [...ERROR_TOAST_BASELINE].filter((k) => !found.has(k)).sort();
    const errs = [];
    if (newViolations.length) {
      errs.push(
        "New mutating apiFetch handler(s) without toastError — wrap the " +
          "call in try/catch and call toastError(action, e) from toast.mjs:\n  " +
          newViolations.join("\n  ")
      );
    }
    if (fixed.length) {
      errs.push(
        "These baseline entries are now clean — remove them from " +
          "ERROR_TOAST_BASELINE so the ratchet keeps shrinking:\n  " +
          fixed.join("\n  ")
      );
    }
    if (errs.length) throw new Error(errs.join("\n\n"));
  });
});
