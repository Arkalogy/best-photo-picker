// @ts-check
// Scan bpp/web/static/js/*.js for every top-level name (function, let,
// const, var) and emit .eslint-globals.json that ESLint reads as the
// global allowlist. Keeps `no-undef` enforceable across our script-tag
// (non-module) JS without hand-maintaining 1,000+ identifiers.
//
// Run automatically via `npm run lint`. The file is committed; CI runs
// the generator and fails if the committed copy drifts from the source.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { extractTopLevelNames } from "./extract-js-names.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const JS_DIR = path.join(ROOT, "bpp", "web", "static", "js");
const MODULES_DIR = path.join(JS_DIR, "modules");
const OUT = path.join(ROOT, ".eslint-globals.json");

const names = new Set();
for (const file of fs.readdirSync(JS_DIR).filter((f) => f.endsWith(".js"))) {
  const content = fs.readFileSync(path.join(JS_DIR, file), "utf8");
  for (const n of extractTopLevelNames(content)) names.add(n);
}

// Cross-realm shared state declared on `window` (no `let`/`var`) so that
// module-side reassignments stay visible to classic-script readers via
// the global-object scope-chain fallback. Hand-listed because the regex-
// based extractor only picks up declarations, not bare property writes.
const WINDOW_GLOBALS = [
  "favorites",
  "multiSelected",
  "lastMultiClickIdx",
  "photos",
  "selectedPaths",
  "overrides",
  "recomputeTimer",
  "faceClusters",
  "peopleFilter",
  "peopleSort",
  "selectedFaceIds",
  "faceRecognitionAvailable",
  "faceInstallable",
  "nudenetAvailable",
  "petsAvailable",
  "currentAlbumId",
  "albumList",
  "activeOperation",
  "state_workdir",
  "currentView",
  "currentViewId",
  "storageOnline",
  "storageCheckInterval",
  "sidebarFaceSort",
  "petClusters",
  "petsFilter",
  "petsSort",
  "editorEdits",
  "editorCropActive",
  "_cropDragging",
  "_cropStartX",
  "_cropStartY",
  "_cropStartRect",
  "_editorAspectRatio",
  "_inpaintMode",
  "_inpaintBrushSize",
  "_inpaintCanvas",
  "_inpaintCtx",
  "_inpaintPainting",
  "_inpaintAvailable",
  "_inpaintTool",
  "currentGridItems",
  "sortedItems",
  "_albumPickerFilepaths",
  "_simClusterMap",
  "editorActive",
  "editorOriginalEdits",
  "_redeyeMode",
  "_editorRevertPending",
  "_cropSavedPerspective",
  "_activeAdjustSlider",
  "lightboxIdx",
  "lbZoom",
  "lbPanX",
  "lbPanY",
  "_lbLeafletMap",
  "_lbMapMarker",
  "LB_ZOOM_MIN",
  "LB_ZOOM_MAX",
  "SCORE_LABELS",
  "_dismissedCount",
  "_dismissedFaces",
  "mergeSourceId",
];
for (const n of WINDOW_GLOBALS) names.add(n);

// ES modules under modules/ are bridged onto window via index.html — every
// `export function NAME` and `export { A, B }` becomes a runtime global
// from the perspective of script-tag-loaded files, so we must allowlist
// them too. Otherwise ESLint flags every call as no-undef.
const EXPORT_FN_RE = /export\s+(?:async\s+)?function\s+(\w+)/g;
const EXPORT_VAR_RE = /export\s+(?:const|let|var)\s+(\w+)/g;
const EXPORT_LIST_RE = /export\s*\{([^}]+)\}/g;
if (fs.existsSync(MODULES_DIR)) {
  for (const file of fs.readdirSync(MODULES_DIR).filter((f) => f.endsWith(".mjs"))) {
    const content = fs.readFileSync(path.join(MODULES_DIR, file), "utf8");
    let m;
    while ((m = EXPORT_FN_RE.exec(content)) !== null) names.add(m[1]);
    while ((m = EXPORT_VAR_RE.exec(content)) !== null) names.add(m[1]);
    while ((m = EXPORT_LIST_RE.exec(content)) !== null) {
      for (const part of m[1].split(",")) {
        const ident = part
          .trim()
          .split(/\s+as\s+/)
          .pop()
          ?.trim();
        if (ident && /^[a-zA-Z_$][\w$]*$/.test(ident)) names.add(ident);
      }
    }
  }
}

const payload = {};
for (const n of [...names].sort()) payload[n] = "writable";
// Trailing newline — matches what git expects and keeps POSIX tools happy.
fs.writeFileSync(OUT, JSON.stringify(payload, null, 2) + "\n");
console.log(`Wrote ${Object.keys(payload).length} globals to ${path.relative(ROOT, OUT)}`);
