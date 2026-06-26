// ESLint flat config for the bpp web UI.
//
// Our JS is a flat set of <script>-loaded files — no modules. ESLint can't
// resolve cross-file references for scripts, so we auto-generate a globals
// allowlist from every top-level name defined across bpp/web/static/js/.
// The list is rebuilt every time `npm run lint` runs
// (see scripts/generate-eslint-globals.mjs).
//
// The whole point of running ESLint here is the `no-undef` rule: it catches
// bugs like loadFaces() that silently fail at runtime because the callee
// doesn't exist anywhere in the JS.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import globals from "globals";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GLOBALS_FILE = path.join(__dirname, ".eslint-globals.json");

let appGlobals = {};
if (fs.existsSync(GLOBALS_FILE)) {
  appGlobals = JSON.parse(fs.readFileSync(GLOBALS_FILE, "utf8"));
}

// Shared rule set — applied to both classic and module configs.
const sharedRules = {
  "no-undef": "error",
  "no-dupe-keys": "error",
  "no-dupe-args": "error",
  "no-dupe-else-if": "error",
  "no-duplicate-case": "error",
  "no-unreachable": "error",
  "no-sparse-arrays": "error",
  "no-self-compare": "error",
  "no-constant-condition": ["error", { checkLoops: false }],
  "no-unsafe-negation": "error",
  "no-cond-assign": "error",
  "use-isnan": "error",
  "valid-typeof": "error",
  "no-unused-vars": "off",
  "no-empty": "off",
};

export default [
  // ES modules under bpp/web/static/js/modules/ — proper imports + exports.
  {
    files: ["bpp/web/static/js/modules/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        L: "readonly",
        VGrid: "readonly",
      },
    },
    rules: {
      ...sharedRules,
      // P8: modules should expose handlers via registerAction or named
      // exports, NOT by assigning to window. Allow window reads + the
      // action-registry's own window.__bppActionRegistry bridge; block
      // other window.X = ... writes. Loosen with eslint-disable-next-line
      // for migration scaffolding (e.g. exposing a singleton during the
      // deprecation window).
      "no-restricted-syntax": [
        "warn",
        {
          selector:
            "AssignmentExpression[left.object.name='window'][left.property.name!='__bppActionRegistry']",
          message:
            "Don't write to window.X from a module. Use registerAction() from " +
            "action-registry.mjs or expose via named exports. See P8.",
        },
      ],
    },
  },
  // Classic script-tag JS — shares one global namespace.
  {
    files: ["bpp/web/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        // Leaflet is loaded from a CDN as a <script>
        L: "readonly",
        // VGrid is referenced behind `typeof VGrid !== "undefined"` guards
        // — it used to exist, now gone, call sites defensive. Safe.
        VGrid: "readonly",
        // Auto-generated from the JS codebase
        ...appGlobals,
      },
    },
    rules: sharedRules,
  },
  {
    ignores: ["node_modules/**", ".venv/**", "desktop/**", "bpp/web/static/js/vendor/**"],
  },
];
