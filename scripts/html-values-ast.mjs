// AST helpers for scripts/html-values.mjs: what counts as markup, as an
// encoder, as a constant.

/** Calls whose result is safe to place in HTML. */
export const ENCODERS = new Set([
  "esc",
  "escapeAttr",
  "escVal",
  "thumbUrl",
  "faceCropUrl",
  "petCropUrl",
  "Number",
  "parseInt",
  "parseFloat",
  "Boolean",
]);
/** Methods whose result is a number or a number's text. */
export const NUMERIC_METHODS = new Set([
  "toFixed",
  "toPrecision",
  "getFullYear",
  "getMonth",
  "getDate",
  "indexOf",
  "findIndex",
]);
/** Methods whose arguments end up in the result too. */
export const TAKES_TEXT = new Set(["replace", "replaceAll", "concat", "padStart", "padEnd"]);
/** Calls that take markup: the argument at this index is written into the page. */
export const MARKUP_SINKS = new Map([
  ["insertAdjacentHTML", 1],
  ["bindPopup", 0],
  ["setContent", 0],
]);
/** Methods that hand back (part of) the value they are called on. */
export const PASS_THROUGH = new Set([
  "trim",
  "slice",
  "substring",
  "toLowerCase",
  "toUpperCase",
  "replace",
  "replaceAll",
  "filter",
  "reverse",
  "sort",
  "concat",
  "padStart",
  "padEnd",
  "toString",
  "flat",
]);

const HTMLISH = /<[a-zA-Z/!]|="|='|&[a-z#0-9]+;/;

export function children(node) {
  const out = [];
  for (const key of Object.keys(node)) {
    if (key === "loc" || key === "range" || key === "parent") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const c of value) if (c && typeof c.type === "string") out.push(c);
    } else if (value && typeof value.type === "string") out.push(value);
  }
  return out;
}

export function isFunction(node) {
  return (
    node &&
    (node.type === "FunctionDeclaration" ||
      node.type === "FunctionExpression" ||
      node.type === "ArrowFunctionExpression")
  );
}

/** The expressions a function can return. */
export function returnsOf(fn) {
  if (fn.type === "ArrowFunctionExpression" && fn.body.type !== "BlockStatement") return [fn.body];
  const out = [];
  (function walk(node) {
    if (node.type === "ReturnStatement") {
      if (node.argument) out.push(node.argument);
      return;
    }
    for (const c of children(node)) if (!isFunction(c)) walk(c);
  })(fn.body);
  return out;
}

export function flattenPlus(node) {
  const parts = [];
  (function flat(n) {
    if (n.type === "BinaryExpression" && n.operator === "+") {
      flat(n.left);
      flat(n.right);
    } else parts.push(n);
  })(node);
  return parts;
}

export function calleeName(node) {
  if (node.type === "Identifier") return node.name;
  if (node.type === "MemberExpression" && !node.computed) return node.property.name;
  return "";
}

/** Is this expression, on its face, an HTML string the app wrote? */
export function looksLikeMarkup(node) {
  if (node.type === "TemplateLiteral")
    return node.quasis.some((q) => HTMLISH.test(q.value.cooked || ""));
  if (node.type === "Literal") return typeof node.value === "string" && HTMLISH.test(node.value);
  if (node.type === "BinaryExpression" && node.operator === "+")
    return flattenPlus(node).some(looksLikeMarkup);
  return false;
}

/** An icon from the app's own ICONS table (SVG markup). */
export function isIcon(node) {
  if (node.type !== "MemberExpression") return false;
  const o = node.object;
  return (
    (o.type === "Identifier" && o.name === "ICONS") ||
    (o.type === "MemberExpression" && !o.computed && o.property.name === "ICONS")
  );
}

/** The function a variable holds, when it is declared as one. */
export function functionOf(variable) {
  for (const def of variable.defs) {
    if (def.type === "FunctionName") return def.node;
    if (def.type === "Variable" && isFunction(def.node.init)) return def.node.init;
  }
  return null;
}

/** The single initialiser of a variable declared once, or null. */
export function initOf(variable) {
  if (variable.defs.length !== 1 || variable.defs[0].type !== "Variable") return null;
  return variable.defs[0].node.init || null;
}

/** Is the variable a table (object or array) of literal constants? */
export function isConstantTableInit(variable) {
  const init = initOf(variable);
  const constant = (n) =>
    n.type === "Literal" ||
    (n.type === "UnaryExpression" && n.argument.type === "Literal") ||
    (n.type === "TemplateLiteral" && n.expressions.length === 0) ||
    (n.type === "ArrayExpression" && n.elements.every((e) => e && constant(e))) ||
    (n.type === "ObjectExpression" &&
      n.properties.every((p) => p.type === "Property" && !p.computed && constant(p.value)));
  return (
    !!init &&
    (init.type === "ObjectExpression" || init.type === "ArrayExpression") &&
    constant(init)
  );
}

/** Is `node` an argument of a call that takes a selector or a log line, not markup? */
export function isNotHtmlArgument(node) {
  const call = node.parent;
  if (!call || call.type !== "CallExpression" || !call.arguments.includes(node)) return false;
  const callee = call.callee;
  if (callee.type !== "MemberExpression" || callee.computed) return false;
  return (
    /^(querySelector|querySelectorAll|closest|matches)$/.test(callee.property.name) ||
    (callee.object.type === "Identifier" && callee.object.name === "console")
  );
}

/** `list.push(...)` / `list.unshift(...)` on a plain variable: the call, or null. */
export function pushCall(id) {
  const p = id.parent;
  const pushes =
    p.type === "MemberExpression" &&
    p.object === id &&
    !p.computed &&
    (p.property.name === "push" || p.property.name === "unshift") &&
    p.parent.type === "CallExpression" &&
    p.parent.callee === p;
  return pushes ? p.parent : null;
}
