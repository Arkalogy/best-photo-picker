// Function lengths for the page modules, parsed (not brace-counted) so a
// `{` inside a template literal can't fool the gate.
//
//   node scripts/function-loc.mjs <cap> <file>...
//
// prints one JSON array of {file, line, name, loc} for every function
// longer than the cap. tests-js/function-loc-cap.test.mjs imports
// `functionLengths` directly; Python's `ast` does the same job for the
// package in tests/test_function_loc_cap.py.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import * as acorn from "acorn";

const FN_TYPES = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"]);

/**
 * @param {any} node
 * @param {string} file
 * @param {number} cap
 * @param {{file: string, line: number, name: string, loc: number}[]} out
 * @param {string} parentName
 */
function visit(node, file, cap, out, parentName = "<anonymous>") {
  if (!node || typeof node.type !== "string") return;
  let name = parentName;
  if (FN_TYPES.has(node.type)) {
    name = node.id?.name || parentName;
    const loc = node.loc.end.line - node.loc.start.line + 1;
    if (loc > cap) out.push({ file, line: node.loc.start.line, name, loc });
  } else if (node.type === "VariableDeclarator" && node.id?.name) {
    name = node.id.name;
  } else if (node.type === "Property" || node.type === "MethodDefinition") {
    name = node.key?.name || node.key?.value || parentName;
  } else if (node.type === "CallExpression") {
    // A callback is named by its call: `addEventListener("keydown", …)`
    // reads as `addEventListener:keydown`.
    const callee = node.callee.property?.name || node.callee.name || parentName;
    const first = node.arguments[0];
    name = typeof first?.value === "string" ? `${callee}:${first.value}` : callee;
  }
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "parent") continue;
    const child = node[key];
    if (Array.isArray(child)) child.forEach((c) => visit(c, file, cap, out, name));
    else if (child && typeof child.type === "string") visit(child, file, cap, out, name);
  }
}

/**
 * Every function longer than `cap` lines in `files`.
 * @param {string[]} files
 * @param {number} cap
 * @returns {{file: string, line: number, name: string, loc: number}[]}
 */
export function functionLengths(files, cap) {
  /** @type {{file: string, line: number, name: string, loc: number}[]} */
  const out = [];
  for (const file of files) {
    const src = readFileSync(file, "utf8");
    const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "module", locations: true });
    visit(ast, file, cap, out);
  }
  return out;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [capArg, ...files] = process.argv.slice(2);
  process.stdout.write(JSON.stringify(functionLengths(files, Number(capArg))));
}
