// One script, analysed: which values reach its HTML strings unencoded.
// Used by scripts/html-values.mjs.
import * as eslintScope from "eslint-scope";
import * as espree from "espree";

import {
  ENCODERS,
  MARKUP_SINKS,
  NUMERIC_METHODS,
  PASS_THROUGH,
  TAKES_TEXT,
  calleeName,
  children,
  flattenPlus,
  functionOf,
  initOf,
  isConstantTableInit,
  isFunction,
  isIcon,
  isNotHtmlArgument,
  looksLikeMarkup,
  pushCall,
  returnsOf,
} from "./html-values-ast.mjs";

const NO_BINDINGS = new Map();

export class ModuleAnalysis {
  /**
   * @param {string} src
   * @param {string} file
   * @param {(fromFile: string, source: string) => ModuleAnalysis | null} resolve
   *   the module an import names, so markup-building functions and
   *   constant tables are recognised across files
   */
  constructor(src, file, resolve = () => null) {
    const sourceType = file.endsWith(".mjs") ? "module" : "script";
    this.src = src;
    this.file = file;
    this.resolve = resolve;
    this.ast = espree.parse(src, { ecmaVersion: "latest", sourceType, loc: true, range: true });
    (function link(node, parent) {
      node.parent = parent;
      for (const c of children(node)) link(c, node);
    })(this.ast, null);
    this.manager = eslintScope.analyze(this.ast, { ecmaVersion: 2022, sourceType });
    this.refOf = new Map();
    for (const scope of this.manager.scopes)
      for (const ref of scope.references) this.refOf.set(ref.identifier, ref);
    this.moduleScope =
      this.manager.scopes.find((s) => s.type === "module") || this.manager.globalScope;
    this.markupExports = new Map();
    this.accumulators = new Map();
  }

  text(node) {
    return this.src.slice(node.range[0], node.range[1]).replace(/\s+/g, " ");
  }

  variableOf(identifier) {
    return this.refOf.get(identifier)?.resolved || null;
  }

  /** The module and exported name an imported binding stands for. */
  imported(variable) {
    const def = variable.defs[0];
    if (!def || def.type !== "ImportBinding" || def.node.type !== "ImportSpecifier") return null;
    const other = this.resolve(this.file, def.parent.source.value);
    return other ? { other, name: def.node.imported.name } : null;
  }

  /** Does the top-level function `name` return markup? */
  returnsMarkup(name) {
    if (!this.markupExports.has(name)) {
      this.markupExports.set(name, false);
      const variable = this.moduleScope.set.get(name);
      const fn = variable && functionOf(variable);
      this.markupExports.set(
        name,
        !!fn && returnsOf(fn).some((r) => this.isMarkup(r, new Set([fn])))
      );
    }
    return this.markupExports.get(name);
  }

  /** Is the top-level `name` a table of constants? */
  isConstantExport(name) {
    const variable = this.moduleScope.set.get(name);
    return !!variable && isConstantTableInit(variable);
  }

  /** What a variable is given: initialisers, assignments, pushed items. */
  writesOf(variable) {
    const exprs = [];
    let opaque = false;
    for (const ref of variable.references) {
      const id = ref.identifier;
      const p = id.parent;
      if (ref.isWrite()) {
        const plain =
          (p.type === "VariableDeclarator" && p.id === id) ||
          (p.type === "AssignmentExpression" && p.left === id);
        const loop = p.parent && /^For(In|Of)Statement$/.test(p.parent.type) ? p.parent : null;
        if (ref.writeExpr && plain && !loop) exprs.push(ref.writeExpr);
        else if (loop && plain && this.isConstantTable(loop.right)) {
          // one row of a constant table
        } else if (p.type !== "UpdateExpression") opaque = true; // destructuring, loop variable
      }
      const push = pushCall(id);
      if (push) exprs.push(...push.arguments);
    }
    return { exprs, opaque };
  }

  /** A variable that is given markup anywhere holds markup. */
  holdsMarkup(variable) {
    if (!this.accumulators.has(variable)) {
      this.accumulators.set(variable, false);
      this.accumulators.set(
        variable,
        this.writesOf(variable).exprs.some((e) => this.isMarkup(e, new Set()))
      );
    }
    return this.accumulators.get(variable);
  }

  /** Is this expression markup the app built (checked where it is built)? */
  isMarkup(node, seen) {
    if (looksLikeMarkup(node)) return true;
    switch (node.type) {
      case "ConditionalExpression":
        return this.isMarkup(node.consequent, seen) || this.isMarkup(node.alternate, seen);
      case "LogicalExpression":
        return this.isMarkup(node.left, seen) || this.isMarkup(node.right, seen);
      case "Identifier": {
        const variable = this.variableOf(node);
        if (!variable || seen.has(variable)) return false;
        seen.add(variable);
        if (variable.defs.some((d) => d.type === "FunctionName")) return false;
        return this.holdsMarkup(variable);
      }
      case "CallExpression":
        return this.isMarkupCall(node, seen);
      case "ChainExpression":
        return this.isMarkup(node.expression, seen);
      case "MemberExpression":
        return isIcon(node);
      default:
        return false;
    }
  }

  isMarkupCall(node, seen) {
    const name = calleeName(node.callee);
    const method = node.callee.type === "MemberExpression";
    if (method && name === "join") return this.isMarkup(node.callee.object, seen);
    if (name === "map" && node.arguments.length) {
      const cb = this.callback(node.arguments[0]);
      if (cb) return returnsOf(cb).some((r) => this.isMarkup(r, seen));
    }
    if (method && PASS_THROUGH.has(name)) return this.isMarkup(node.callee.object, seen);
    const fn = this.localFunction(node.callee);
    if (fn && !seen.has(fn)) {
      seen.add(fn);
      return returnsOf(fn).some((r) => this.isMarkup(r, seen));
    }
    if (node.callee.type === "Identifier") {
      const variable = this.variableOf(node.callee);
      const from = variable && this.imported(variable);
      if (from) return from.other.returnsMarkup(from.name);
    }
    return false;
  }

  /** A callback: written inline, or a function of this file by name. */
  callback(node) {
    return isFunction(node) ? node : this.localFunction(node);
  }

  /** The function a callee names, when it is defined in this file. */
  localFunction(callee) {
    if (callee.type !== "Identifier") return null;
    const variable = this.variableOf(callee);
    return variable ? functionOf(variable) : null;
  }

  /** Is this object a table of constants (here or in the module it comes from)? */
  isConstantTable(node) {
    if (node.type !== "Identifier") return false;
    const variable = this.variableOf(node);
    if (!variable) return false;
    const from = this.imported(variable);
    if (from) return from.other.isConstantExport(from.name);
    if (isConstantTableInit(variable)) return true;
    // One row picked out of a constant table: TABLE.find(...)
    const init = initOf(variable);
    return (
      !!init &&
      init.type === "CallExpression" &&
      init.callee.type === "MemberExpression" &&
      calleeName(init.callee) === "find" &&
      this.isConstantTable(init.callee.object)
    );
  }

  /**
   * The raw values an expression can carry into HTML. `env` binds a
   * called function's parameters to the arguments of the call.
   */
  rawValues(node, env, seen) {
    if (!node) return [];
    if (looksLikeMarkup(node) && node.type !== "BinaryExpression") return []; // a root of its own
    const all = (nodes) => nodes.flatMap((n) => this.rawValues(n, env, seen));
    switch (node.type) {
      case "Literal":
      case "UnaryExpression":
      case "UpdateExpression":
        return [];
      case "TemplateLiteral":
        return all(node.expressions);
      case "ConditionalExpression":
        return all([node.consequent, node.alternate]);
      case "LogicalExpression":
        return node.operator === "&&" ? all([node.right]) : all([node.left, node.right]);
      case "BinaryExpression":
        return node.operator === "+" ? all(flattenPlus(node)) : []; // else arithmetic, comparison
      case "AwaitExpression":
        return all([node.argument]);
      case "ChainExpression":
        return all([node.expression]);
      case "AssignmentExpression":
        return all([node.right]);
      case "ArrayExpression":
        return all(node.elements);
      case "MemberExpression":
        if (!node.computed && (node.property.name === "length" || node.property.name === "size"))
          return [];
        return this.isConstantTable(node.object) || isIcon(node) ? [] : [node];
      case "Identifier":
        return this.rawIdentifier(node, env, seen);
      case "CallExpression":
        return this.rawCall(node, env, seen);
      default:
        return [node];
    }
  }

  rawIdentifier(node, env, seen) {
    if (node.name === "undefined") return [];
    const variable = this.variableOf(node);
    if (!variable) return [node];
    if (env.has(variable)) {
      const bound = env.get(variable);
      return this.rawValues(bound.node, bound.env, seen);
    }
    if (seen.has(variable)) return [];
    seen = new Set(seen).add(variable);
    if (this.holdsMarkup(variable)) return [];
    const def = variable.defs[0];
    if (!def) return [node];
    if (def.type === "Parameter") return this.rawParameter(node, variable, def.node, seen);
    if (def.type !== "Variable") return [node]; // import, function name, catch clause
    const { exprs, opaque } = this.writesOf(variable);
    if (opaque) return [node];
    return exprs.flatMap((e) => this.rawValues(e, env, seen));
  }

  /**
   * Every call of a function of this file, or null when they can't all
   * be known (exported, passed around as a value, never called here).
   */
  callsOf(fn) {
    let fnVariable = null;
    if (fn.type === "FunctionDeclaration") fnVariable = this.manager.getDeclaredVariables(fn)[0];
    else if (fn.parent && fn.parent.type === "VariableDeclarator") {
      fnVariable = this.manager.getDeclaredVariables(fn.parent)[0];
    }
    if (!fnVariable) return null;
    const exported =
      (fn.parent && fn.parent.type === "ExportNamedDeclaration") ||
      (fn.parent && fn.parent.parent && fn.parent.parent.parent?.type === "ExportNamedDeclaration");
    const calls = [];
    for (const ref of fnVariable.references) {
      const id = ref.identifier;
      if (ref.isWrite()) continue;
      if (id.parent.type === "CallExpression" && id.parent.callee === id) calls.push(id.parent);
      else return null;
    }
    return exported || calls.length === 0 ? null : calls;
  }

  /** The arguments a parameter is given, or null when the callers aren't all known. */
  argumentsFor(variable) {
    const def = variable.defs[0];
    if (!def || def.type !== "Parameter" || variable.defs.length !== 1) return null;
    const fn = def.node;
    const index = fn.params.findIndex((p) => p === variable.identifiers[0]);
    const calls = index < 0 ? null : this.callsOf(fn); // index < 0: destructured or defaulted
    return calls && calls.map((c) => c.arguments[index]).filter(Boolean);
  }

  /** A parameter: follow the calls made in this file, when all are known. */
  rawParameter(node, variable, fn, seen) {
    const index = fn.params.findIndex((p) => p === variable.identifiers[0]);
    const call = fn.parent;
    if (call && call.type === "CallExpression" && call.arguments.includes(fn)) {
      // An inline callback: the 2nd parameter of map/forEach is the index.
      const name = calleeName(call.callee);
      const indexed = /^(map|forEach|filter|some|every|findIndex|flatMap)$/.test(name);
      return index === 1 && indexed ? [] : [node];
    }
    const given = this.argumentsFor(variable);
    return given ? given.flatMap((a) => this.rawValues(a, NO_BINDINGS, seen)) : [node];
  }

  /**
   * A call the checker can't see into. If it calls a parameter (a
   * callback handed to a function of this file), what each caller's
   * callback returns is what is written.
   */
  rawCallOfParameter(node, env, seen) {
    const variable = node.callee.type === "Identifier" ? this.variableOf(node.callee) : null;
    const given = variable && !seen.has(variable) ? this.argumentsFor(variable) : null;
    if (given && given.every((a) => this.callback(a))) {
      const next = new Set(seen).add(variable);
      return given.flatMap((a) =>
        returnsOf(this.callback(a)).flatMap((r) => this.rawValues(r, NO_BINDINGS, next))
      );
    }
    return this.isMarkup(node, new Set()) ? [] : [node]; // markup built in another file
  }

  rawCall(node, env, seen) {
    const name = calleeName(node.callee);
    const method = node.callee.type === "MemberExpression";
    const object = method ? node.callee.object : null;
    const all = (nodes) => nodes.flatMap((n) => this.rawValues(n, env, seen));
    if (!method && ENCODERS.has(name)) return [];
    if (object && object.type === "Identifier" && object.name === "Math") return [];
    if (object && object.type === "Identifier" && object.name === "CSS" && name === "escape")
      return [];
    if (method && NUMERIC_METHODS.has(name)) return [];
    if (!method && (name === "String" || name === "authedSrc")) return all(node.arguments);
    if (method && name === "join") return all([object, ...node.arguments]);
    if (method && name === "map" && node.arguments.length && this.callback(node.arguments[0])) {
      return all(returnsOf(this.callback(node.arguments[0])));
    }
    if (method && PASS_THROUGH.has(name)) {
      // The text it is called on, and for replace() and the like what is put in.
      const given = TAKES_TEXT.has(name) ? node.arguments : [];
      return all([object, ...given.flatMap((a) => (isFunction(a) ? returnsOf(a) : [a]))]);
    }
    const fn = this.localFunction(node.callee);
    if (!fn) return this.rawCallOfParameter(node, env, seen);
    if (seen.has(fn)) return [node];
    const inner = new Map(env);
    const scopeVars = this.manager.getDeclaredVariables(fn);
    fn.params.forEach((p, i) => {
      if (p.type !== "Identifier") return;
      const variable = scopeVars.find((v) => v.identifiers[0] === p);
      if (variable && node.arguments[i]) inner.set(variable, { node: node.arguments[i], env });
    });
    const nextSeen = new Set(seen).add(fn);
    return returnsOf(fn).flatMap((r) => this.rawValues(r, inner, nextSeen));
  }

  /** The expressions this node places into markup (its "roots"), if any. */
  placedInMarkup(node) {
    if (isNotHtmlArgument(node)) return []; // a CSS selector or a log line
    if (node.type === "TemplateLiteral" && looksLikeMarkup(node)) return node.expressions;
    if (node.type === "BinaryExpression" && node.operator === "+") {
      const inner = node.parent.type === "BinaryExpression" && node.parent.operator === "+";
      return !inner && looksLikeMarkup(node) ? flattenPlus(node) : [];
    }
    if (node.type === "AssignmentExpression") {
      const left = node.left;
      const toHtml =
        left.type === "MemberExpression" &&
        !left.computed &&
        (left.property.name === "innerHTML" || left.property.name === "outerHTML");
      const handlerName =
        left.type === "MemberExpression" &&
        !left.computed &&
        /^(action|on[a-z]+)$/.test(left.property.name) &&
        left.object.type === "MemberExpression" &&
        left.object.property.name === "dataset";
      const variable = left.type === "Identifier" ? this.variableOf(left) : null;
      const placed = toHtml || handlerName || (variable && this.holdsMarkup(variable));
      return placed ? [node.right] : [];
    }
    if (node.type === "VariableDeclarator" && node.init && node.id.type === "Identifier") {
      const variable = this.manager.getDeclaredVariables(node)[0];
      return variable && this.holdsMarkup(variable) ? [node.init] : [];
    }
    if (node.type !== "CallExpression") return [];
    const name = calleeName(node.callee);
    const sink = node.arguments[MARKUP_SINKS.get(name)];
    if (sink) return [sink];
    if (name === "setAttribute" && node.arguments.length === 2) {
      // A handler name: what the dispatchers in globals.js will call.
      const attr = node.arguments[0];
      const names = attr.type === "Literal" && /^data-(action|on)/.test(String(attr.value));
      return names ? [node.arguments[1]] : [];
    }
    const pushed = name === "push" || name === "unshift";
    if (
      pushed &&
      node.callee.type === "MemberExpression" &&
      node.callee.object.type === "Identifier"
    ) {
      const variable = this.variableOf(node.callee.object);
      return variable && this.holdsMarkup(variable) ? node.arguments : [];
    }
    return [];
  }

  /** Every raw value this script writes into HTML: [{line, expr, at}]. */
  analyze() {
    const found = new Map();
    const visit = (node) => {
      for (const direct of this.placedInMarkup(node)) {
        for (const raw of this.rawValues(direct, NO_BINDINGS, new Set())) {
          found.set(`${raw.range}/${direct.range}`, {
            line: raw.loc.start.line,
            expr: this.text(raw),
            at: { range: direct.range, text: this.text(direct) },
          });
        }
      }
      for (const c of children(node)) visit(c);
    };
    visit(this.ast);
    return [...found.values()].sort((a, b) => a.line - b.line || a.expr.localeCompare(b.expr));
  }
}
