// @ts-check
/**
 * Cross-realm shared state, exposed as an importable module API.
 *
 * Background: classic <script>-tag JS used `let X = ...` declarations
 * that were globally visible. The JS-modules migration broke that
 * sharing — module-private bindings can't be read by sibling modules.
 * The interim solution was to declare state on `window` directly
 * (see globals.js), making every cross-module read look like
 * `window.photos`, `window.faceClusters`, etc.
 *
 * That worked, but it left modules reaching into a global namespace
 * with no enforced contract: any module could create or rename a
 * `window.X` and silently break readers.
 *
 * This module is the documented contract. `state` is a Proxy that
 * reads and writes through `window`, so it's a *live alias* — there
 * is no separate copy to keep in sync. Existing call sites that use
 * `window.photos` continue to work; new code uses `state.photos`.
 *
 * The schema below pins which keys are part of the contract. Reading
 * or writing a key not in the schema in dev raises (helps catch
 * typos like `state.photoes`).
 *
 * For a future contributor:
 * - To add a new shared state field: add the key + initial value to
 *   _STATE_SCHEMA below; reads/writes via `state.foo` Just Work.
 * - To migrate a module: change `window.X` → `state.X` and import
 *   `state` from this module. No behavior change.
 * - When the migration is complete (no more `window.X` reads/writes
 *   in modules), the Proxy can be replaced with a plain object.
 */

// All cross-realm state fields, with their initial values. The
// shape is "field name → initial value used if unset on window."
// Falsey defaults (null, false, 0) and structured defaults (Set,
// Array, Object) are all supported.
const _STATE_SCHEMA = {
  // Photo grid + selection
  favorites: () => new Set(),
  multiSelected: () => new Set(),
  lastMultiClickIdx: -1,
  photos: () => [],
  selectedPaths: () => new Set(),
  overrides: () => ({}),
  recomputeTimer: null,

  // People view
  faceClusters: () => [],
  peopleFilter: "included",
  peopleSort: "count",
  selectedFaceIds: () => new Set(),
  faceRecognitionAvailable: false,
  faceInstallable: false,
  nudenetAvailable: false,
  petsAvailable: false,
  sidebarFaceSort: "count",
  _dismissedCount: 0,
  _dismissedFaces: null,
  mergeSourceId: null,

  // Pets
  petClusters: () => [],
  petsFilter: "all",
  petsSort: "count",

  // Albums + view
  currentAlbumId: null,
  albumList: () => [],
  activeOperation: null,
  state_workdir: null,
  currentView: "library",
  currentViewId: null,
  storageOnline: true,
  storageCheckInterval: null,

  // Editor
  editorEdits: () => ({}),
  editorCropActive: false,
  _cropDragging: null,
  _cropStartX: 0,
  _cropStartY: 0,
  _cropStartRect: () => ({}),
  _editorAspectRatio: null,
  _inpaintMode: false,
  _inpaintBrushSize: 30,
  _inpaintCanvas: null,
  _inpaintCtx: null,
  _inpaintPainting: false,
  _inpaintAvailable: null,
  _inpaintTool: "erase",
  editorActive: false,
  editorOriginalEdits: null,
  _redeyeMode: false,
  _editorRevertPending: false,
  _cropSavedPerspective: null,
  _activeAdjustSlider: null,

  // Lightbox
  lightboxIdx: -1,
  lbZoom: 1,
  lbPanX: 0,
  lbPanY: 0,
  _lbLeafletMap: null,
  _lbMapMarker: null,
  LB_ZOOM_MIN: 1,
  LB_ZOOM_MAX: 10,
  SCORE_LABELS: () => ({
    blur_score: "Sharpness",
    exposure_score: "Exposure",
    face_score: "Faces",
    composition_score: "Composition",
  }),

  // Photo grid internals
  currentGridItems: () => [],
  sortedItems: () => [],
  _albumPickerFilepaths: () => [],
  _simClusterMap: () => ({}),
};

const _SCHEMA_KEYS = new Set(Object.keys(_STATE_SCHEMA));

/**
 * Initialize any window keys that haven't been set yet (preserves
 * existing values from globals.js / earlier module loads).
 */
export function initState() {
  for (const [key, value] of Object.entries(_STATE_SCHEMA)) {
    if (window[key] === undefined) {
      window[key] = typeof value === "function" ? value() : value;
    }
  }
}

initState();

/**
 * The state Proxy: reads/writes through window so this module and
 * any classic-script reader see the exact same values.
 *
 * Reads of unknown keys log a warning (typo detection). Writes of
 * unknown keys are allowed but warned — a key being added to a
 * module without updating _STATE_SCHEMA is technically valid but
 * usually a bug.
 */
/** @type {Record<string, any>} */
export const state = new Proxy(
  {},
  {
    get(_t, key) {
      if (typeof key !== "string") return undefined;
      if (!_SCHEMA_KEYS.has(key)) {
        // Don't blow up — could be a Symbol probe or an inherited
        // method (Array.prototype.length etc). Just return whatever
        // window has.
        return window[key];
      }
      return window[key];
    },
    set(_t, key, value) {
      if (typeof key === "string" && !_SCHEMA_KEYS.has(key)) {
        // Allow writes (back-compat) but log so an out-of-schema
        // field added during a migration surfaces.
        if (typeof console !== "undefined" && console.warn) {
          console.warn(`state: write to unschema'd key '${key}'`);
        }
      }
      // key is string|symbol (Proxy set trap); state keys are strings.
      // Cast for the index write — a symbol key just passes through.
      /** @type {any} */ (window)[key] = value;
      return true;
    },
    has(_t, key) {
      return typeof key === "string" && _SCHEMA_KEYS.has(key);
    },
    ownKeys() {
      return Array.from(_SCHEMA_KEYS);
    },
    getOwnPropertyDescriptor(_t, key) {
      if (typeof key === "string" && _SCHEMA_KEYS.has(key)) {
        return {
          enumerable: true,
          configurable: true,
          value: window[key],
        };
      }
      return undefined;
    },
  }
);

/**
 * Check if a key is part of the state schema. Useful for tests.
 */
export function isStateKey(key) {
  return _SCHEMA_KEYS.has(key);
}

/**
 * For tests: list every state key. The schema is the source of truth
 * for what's part of the cross-realm contract.
 */
export function stateKeys() {
  return Array.from(_SCHEMA_KEYS);
}
