// @ts-check
// Shared helper: given JS source text, return the set of every top-level
// identifier it declares. Used by both the ESLint globals generator and
// the Vitest test harness to keep their name-discovery in lock-step.
//
// Handles:
//   function NAME(...)              — including `async function`
//   let / const / var NAME = ...    — single + multi-declarator
//   let / const / var { A, B }      — basic object destructuring
//   let / const / var [ A, B ]      — basic array destructuring
//
// Out of scope: nested destructuring, rest patterns, defaults inside
// destructuring (our runtime JS doesn't use these at top level).

const FUNC_RE = /(?:^|\n)(?:async\s+)?function\s+(\w+)\s*\(/g;
const VAR_BLOCK_RE = /(?:^|\n)(?:const|let|var)\s+([^;\n]+)/g;
const DESTRUCTURE_RE = /[{[]([^}\]]+)[}\]]/g;
const IDENT_IN_VARS_RE = /([a-zA-Z_$][\w$]*)\s*(?:=|,|$)/g;
const PLAIN_IDENT_RE = /[a-zA-Z_$][\w$]*/g;

const RESERVED = new Set([
  "true",
  "false",
  "null",
  "undefined",
  "new",
  "typeof",
  "void",
  "this",
  "delete",
  "in",
  "of",
]);

/**
 * Extract every top-level identifier declared in a JS source string.
 *
 * Used by both the ESLint globals generator and the Vitest test harness
 * to keep their name discovery in lock-step.
 *
 * @param {string} content - Raw JavaScript source text.
 * @returns {Set<string>} The set of declared identifiers.
 */
export function extractTopLevelNames(content) {
  const names = new Set();

  let m;
  while ((m = FUNC_RE.exec(content)) !== null) {
    if (m[1]) names.add(m[1]);
  }

  VAR_BLOCK_RE.lastIndex = 0;
  while ((m = VAR_BLOCK_RE.exec(content)) !== null) {
    const block = m[1];

    // Pull destructured names first (e.g. `const { a, b } = foo()`).
    DESTRUCTURE_RE.lastIndex = 0;
    let d;
    while ((d = DESTRUCTURE_RE.exec(block)) !== null) {
      PLAIN_IDENT_RE.lastIndex = 0;
      let id;
      while ((id = PLAIN_IDENT_RE.exec(d[1])) !== null) {
        if (!RESERVED.has(id[0])) names.add(id[0]);
      }
    }

    // Then the plain `NAME = ...` / `NAME,` declarators.
    IDENT_IN_VARS_RE.lastIndex = 0;
    let id;
    while ((id = IDENT_IN_VARS_RE.exec(block)) !== null) {
      if (!RESERVED.has(id[1])) names.add(id[1]);
    }
  }

  return names;
}
