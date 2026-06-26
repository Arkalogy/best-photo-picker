const APP_CONFIG = {
  name: "Best Photo Picker",
};
// Bridge to window so ES modules (under modules/*.mjs) can read it via
// `window.APP_CONFIG`. Classic top-level `const` is in the script-record
// lexical environment, NOT on the global object — so without this,
// modules see `undefined`.
window.APP_CONFIG = APP_CONFIG;

const ICONS = {
  library: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="1.5" width="5" height="5" rx="1"/><rect x="9.5" y="1.5" width="5" height="5" rx="1"/><rect x="1.5" y="9.5" width="5" height="5" rx="1"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/></svg>',
  people: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="5" r="2.5"/><path d="M3 14c0-2.76 2.24-5 5-5s5 2.24 5 5"/></svg>',
  heart: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.7l-5.7-5.2A3.1 3.1 0 012 6.2C2 4.43 3.43 3 5.2 3c1.1 0 2.1.56 2.8 1.44A3.18 3.18 0 0110.8 3C12.57 3 14 4.43 14 6.2c0 .86-.35 1.64-.92 2.2L8 13.7z"/></svg>',
  folder: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4.5V12a1.5 1.5 0 001.5 1.5h9A1.5 1.5 0 0014 12V6a1.5 1.5 0 00-1.5-1.5H8L6.5 3H3.5A1.5 1.5 0 002 4.5z"/></svg>',
  calendar: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="11" rx="1.5"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1.5" x2="5" y2="4.5"/><line x1="11" y1="1.5" x2="11" y2="4.5"/></svg>',
  star: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5l1.85 4.1L14.2 6l-3.1 2.85.85 4.15L8 10.75 4.05 13l.85-4.15L1.8 6l4.35-.4L8 1.5z"/></svg>',
  trash: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h10M6 4V3a1 1 0 011-1h2a1 1 0 011 1v1M4 4v9a1 1 0 001 1h6a1 1 0 001-1V4"/></svg>',
  importArrow: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v8M5 7l3 3 3-3"/><path d="M2 11v2a1 1 0 001 1h10a1 1 0 001-1v-2"/></svg>',
  exportArrow: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 10V2M5 5l3-3 3 3"/><path d="M2 11v2a1 1 0 001 1h10a1 1 0 001-1v-2"/></svg>',
  inspector: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="14" height="14" rx="2"/><line x1="10" y1="1" x2="10" y2="15"/></svg>',
  more: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="3" cy="8" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="13" cy="8" r="1.3"/></svg>',
  sort: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3v10M3 5l2-2 2 2M11 13V3M9 11l2 2 2-2"/></svg>',
  filter: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h12M4 6.5h8M6 10h4M7 13.5h2"/></svg>',
  analyze: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4a6 6 0 0111.5 1"/><path d="M14 12A6 6 0 012.5 11"/><path d="M11 2l2.5 3-3 .5"/><path d="M5 14l-2.5-3 3-.5"/></svg>',
  inbox: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 10l2.5-6h7L14 10"/><path d="M2 10v3a1 1 0 001 1h10a1 1 0 001-1v-3H11l-1 2H6l-1-2H2z"/></svg>',
  paw: '<svg viewBox="0 0 16 16" fill="currentColor"><ellipse cx="5" cy="3.5" rx="1.5" ry="1.8"/><ellipse cx="11" cy="3.5" rx="1.5" ry="1.8"/><ellipse cx="2.8" cy="7" rx="1.3" ry="1.6"/><ellipse cx="13.2" cy="7" rx="1.3" ry="1.6"/><path d="M4.5 10.5c0-2 1.6-3.5 3.5-3.5s3.5 1.5 3.5 3.5c0 1.5-1.2 3-2 3.5-.5.3-1 .5-1.5.5s-1-.2-1.5-.5c-.8-.5-2-2-2-3.5z"/></svg>',
  search: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6.5" cy="6.5" r="4.5"/><line x1="10" y1="10" x2="14" y2="14"/></svg>',
  group: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="4.5" r="2"/><path d="M1.5 13c0-2.5 2-4.5 4.5-4.5s4.5 2 4.5 4.5"/><circle cx="11" cy="5" r="1.8"/><path d="M11 8.5c1.9 0 3.5 1.6 3.5 3.5"/></svg>',
  map: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5C5.5 1.5 3.5 3.5 3.5 6c0 3.5 4.5 8.5 4.5 8.5s4.5-5 4.5-8.5c0-2.5-2-4.5-4.5-4.5z"/><circle cx="8" cy="6" r="1.5"/></svg>',
  clock: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></svg>',
  hidden: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 2l12 12"/><path d="M6.5 6.5a2 2 0 002.8 2.8"/><path d="M3.5 5.5C2.5 6.5 1.5 8 1.5 8s2.5 4.5 6.5 4.5c1 0 1.9-.3 2.7-.7M12.5 10.5C13.5 9.5 14.5 8 14.5 8S12 3.5 8 3.5c-.5 0-1 .1-1.5.2"/></svg>',
  video: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="3" width="9.5" height="10" rx="1.5"/><path d="M11 6.5l3.5-2v7l-3.5-2"/></svg>',
  screenshot: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="1.5" width="12" height="13" rx="1.5"/><path d="M5 5.5h6M5 8h4"/><circle cx="11" cy="11" r="1"/></svg>',
  duplicate: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="1.5" y="3.5" width="9" height="11" rx="1.5"/><path d="M5.5 3.5V2.5a1.5 1.5 0 011.5-1.5h6a1.5 1.5 0 011.5 1.5v8a1.5 1.5 0 01-1.5 1.5h-1"/></svg>',
  moments: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5l6 3-6 3-6-3 6-3z"/><path d="M2 8l6 3 6-3"/><path d="M2 11l6 3 6-3"/></svg>',
  noFace: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="5.5" r="2.5"/><path d="M3.5 14.5c0-2.5 2-4.5 4.5-4.5s4.5 2 4.5 4.5"/><line x1="2" y1="2" x2="14" y2="14"/></svg>',
  document: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 1.5h5.5L13 5v9a1.5 1.5 0 01-1.5 1.5h-6A1.5 1.5 0 014 14V3a1.5 1.5 0 011.5-1.5z"/><path d="M9.5 1.5V5H13"/><line x1="6.5" y1="8" x2="10.5" y2="8"/><line x1="6.5" y1="10.5" x2="10.5" y2="10.5"/></svg>',
  pencil: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 1.5l3 3-9 9H2.5v-3l9-9z"/><path d="M9.5 3.5l3 3"/></svg>',
  picks: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><path d="M5.5 8l2 2 3.5-4"/></svg>',
  settings: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="2"/><path d="M13.5 8a5.5 5.5 0 00-.1-.8l1.3-1-.7-1.2-1.5.5a5.5 5.5 0 00-1.2-.7L11 3.3h-1.4l-.3 1.5a5.5 5.5 0 00-1.2.7l-1.5-.5-.7 1.2 1.3 1a5.5 5.5 0 000 1.6l-1.3 1 .7 1.2 1.5-.5c.4.3.8.5 1.2.7l.3 1.5H11l.3-1.5c.4-.2.8-.4 1.2-.7l1.5.5.7-1.2-1.3-1a5.5 5.5 0 00.1-.8z"/></svg>',
  tag: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 9.2V2.5A1 1 0 012.5 1.5h6.7a1 1 0 01.7.3l5.3 5.3a1 1 0 010 1.4l-5.3 5.3a1 1 0 01-1.4 0L1.8 9.9a1 1 0 01-.3-.7z"/><circle cx="5" cy="5" r="1" fill="currentColor" stroke="none"/></svg>',
};
// Bridge to window so ES modules can read window.ICONS. slideshow.mjs
// extends this object with its own keys (ssPlay, ssSlideshow, etc.) —
// merge so modules see the full union, not whichever was last.
window.ICONS = Object.assign(window.ICONS || {}, ICONS);

// MONTHS_SHORT, MONTHS_FULL → modules/date-format.mjs (bridged onto window)

// State that needs to round-trip cross-realm (modules ↔ classic scripts):
// declare on `window` directly (no `let`) so module assignments propagate
// back into classic readers via the global-object scope-chain fallback.
// Classic scripts can still bare-read these (`if (favorites.size)`) — the
// scope chain falls through the script-record lexical environment to the
// global object. Bare writes also work in sloppy mode (the default for
// classic <script> tags) since they auto-create a global.
window.favorites = new Set();
window.multiSelected = new Set();
window.lastMultiClickIdx = -1;
window.photos = [];
window.selectedPaths = new Set();
window.overrides = {};
window.recomputeTimer = null;
window.faceClusters = [];
window.peopleFilter = "included"; // "all" | "included" | "excluded"
window.peopleSort = "count"; // "count" | "name"
window.selectedFaceIds = new Set();
window.faceRecognitionAvailable = false;
window.faceInstallable = false;
window.nudenetAvailable = false;
window.petsAvailable = false;
window.currentAlbumId = null;
window.albumList = [];
window.activeOperation = null; // 'analyze' or 'import'
window.state_workdir = null;
window.currentView = "library"; // 'library' | 'album' | 'people' | 'groups'
window.currentViewId = null;
window.storageOnline = true;
window.storageCheckInterval = null;
window.sidebarFaceSort = "count"; // "count" | "name"
window.petClusters = [];
window.petsFilter = "all";
window.petsSort = "count";
// Editor state: editorEdits is the working-copy, mutated in place by
// editor.js + editor-crop.mjs + editor-inpaint.js + editor-redeye.mjs.
// Reassignments happen in editor.js's load/reset/preset paths.
window.editorEdits = {};
window.editorCropActive = false;
window._cropDragging = null; // null | 'move' | 'nw' | 'ne' | 'sw' | 'se'
window._cropStartX = 0;
window._cropStartY = 0;
window._cropStartRect = {};
window._editorAspectRatio = null;
window._inpaintMode = false;
window._inpaintBrushSize = 30;
window._inpaintCanvas = null;
window._inpaintCtx = null;
window._inpaintPainting = false;
window._inpaintAvailable = null; // null = unchecked, true/false after check
window._inpaintTool = "erase"; // "erase" or "retouch"
window.editorActive = false;
window.editorOriginalEdits = null; // Snapshot to revert on Cancel
window._redeyeMode = false;
window._editorRevertPending = false; // guard against double-click on Revert
window._cropSavedPerspective = null; // stashed perspective_v/h while crop tab is active
window._activeAdjustSlider = null; // null = icon grid, "exposure" = that slider open
// Lightbox state — declared on window so editor.mjs / compare.mjs / etc. all
// share it via the global-object scope-chain fallback.
window.lightboxIdx = -1;
window.lbZoom = 1;
window.lbPanX = 0;
window.lbPanY = 0;
window._lbLeafletMap = null;
window._lbMapMarker = null;
window.LB_ZOOM_MIN = 1;
window.LB_ZOOM_MAX = 10;
window.SCORE_LABELS = {
  blur_score: "Sharpness",
  exposure_score: "Exposure",
  face_score: "Faces",
  composition_score: "Composition",
};
// People-view state — dismissedCount/Faces are read by faces.mjs after
// loading clusters; mergeSourceId is read by toolbar.mjs to decide
// whether the merge picker is open.
window._dismissedCount = 0;
window._dismissedFaces = null;
window.mergeSourceId = null;
// Photo grid state (currentGridItems, sortedItems, _albumPickerFilepaths,
// _simClusterMap) declared on window so classic albums.js writes
// (`vgrid.items = currentGridItems = sortedItems = ...`) and module-side
// reads stay in sync.
window.currentGridItems = [];
window.sortedItems = [];
window._albumPickerFilepaths = [];
window._simClusterMap = {};
// serveMode is injected by the template before this file loads.
// faceGroups → modules/groups.mjs (read via _getFaceGroups, write via _setFaceGroups)
const FACE_MIN_PHOTOS = 4; // Hide clusters below this threshold (like Apple Photos)

// ── Library-level settings (persisted in DB) ──
// _dbSettings, loadSettings, getSetting, saveSetting → modules/settings-client.mjs
// applyTheme, setTheme, initTheme → modules/theme.mjs
// (both bridged onto window via index.html's module bootstrap)

// ── Server health check ──
let _healthFailures = 0;
let _serverDownPolling = false;
function startServerHealthCheck() {
  setInterval(async () => {
    if (_serverDownPolling) return; // auto-reconnect loop handles it
    try {
      const r = await fetch("/api/v1/status", {signal: AbortSignal.timeout(3000), headers: {"X-Auth-Token": _authToken}});
      if (r.ok) { _healthFailures = 0; return; }
    } catch {}
    _healthFailures++;
    if (_healthFailures >= 3) {
      _startAutoReconnect();
    }
  }, 5000);
}

function _startAutoReconnect() {
  if (_serverDownPolling) return;
  _serverDownPolling = true;
  const overlay = document.getElementById("server-down-overlay");
  overlay.classList.add("visible");
  // Update overlay to show reconnecting state
  const inner = overlay.querySelector(".server-down-inner");
  if (inner) {
    inner.querySelector("h2").textContent = "Reconnecting\u2026";
    inner.querySelector("p").textContent = "Waiting for the server to come back.";
    const btn = inner.querySelector("button");
    if (btn) btn.style.display = "none";
    // Add spinner if not present
    if (!inner.querySelector(".server-down-spinner")) {
      const sp = document.createElement("div");
      sp.className = "server-down-spinner";
      inner.insertBefore(sp, inner.firstChild);
    }
  }
  _pollUntilServerBack();
}

async function _pollUntilServerBack() {
  // Poll "/" (auth-exempt) because after server restart the auth token
  // changes — /api/status would return 403 with the stale token.
  // A successful fetch of "/" means the server is alive; reload gets
  // the fresh token from the re-rendered index page.
  while (_serverDownPolling) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const r = await fetch("/", {signal: AbortSignal.timeout(3000)});
      if (r.ok) {
        _serverDownPolling = false;
        _healthFailures = 0;
        location.reload();
        return;
      }
    } catch {}
  }
}

// ── Cmd+R / Ctrl+R / F5 reload (Tauri webview doesn't handle natively) ──
document.addEventListener("keydown", (e) => {
  if (((e.metaKey || e.ctrlKey) && e.key === "r") || e.key === "F5") {
    e.preventDefault(); location.reload();
  }
});

// parseSSE → modules/format-helpers.mjs (bridged onto window)

// ── Navigation state persistence ──

// ── CSP-safe event dispatch ──
// Replaces all inline onclick/oninput/etc. attributes.
// Use data-action="fnName" [data-arg0="v0"] [data-arg1="v1"] ...
// data-stop-propagation and data-prevent-default are handled before dispatch.
// Numeric-looking arg strings are coerced to numbers; "true"/"false" → booleans.
function _bppCoerceArg(s) {
  if (s === "true") return true;
  if (s === "false") return false;
  if (s !== "" && !isNaN(+s)) return +s;
  return s;
}
function _bppCollectArgs(el) {
  var args = [], i = 0;
  while (el.dataset["arg" + i] !== undefined) {
    args.push(_bppCoerceArg(el.dataset["arg" + i]));
    i++;
  }
  return args;
}
document.addEventListener("click", function _bppDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-action],[data-stop-propagation],[data-prevent-default]");
  if (!el) return;
  // Context menus have their own dedicated click handlers — skip them here
  if (el.closest(".ctx-menu")) return;
  // data-stop-propagation: stop the event AT the marked element on the way
  // BACK UP (bubble), like the inline onclick="event.stopPropagation()" it
  // replaced. This dispatcher runs in CAPTURE phase — calling
  // e.stopPropagation() here would kill the event at document before it
  // ever DESCENDS to the target, silencing every native listener inside
  // the marked container (regression: Leaflet map +/− zoom controls in the
  // lightbox panel were dead; drag/wheel worked because only click routes
  // through this dispatcher).
  if ("stopPropagation" in el.dataset) {
    el.addEventListener("click", function _bppBubbleStop(ev) { ev.stopPropagation(); }, { once: true });
  }
  if ("preventDefault" in el.dataset) e.preventDefault();
  var name = el.dataset.action;
  if (!name) return;
  // P8: consult the typed action registry FIRST. Fall back to window[name]
  // during the deprecation window so the ~245 unmigrated handlers still
  // resolve. Once a handler is in the registry, its window.X = X bridge
  // can be deleted. ``?? undefined`` instead of ``|| undefined`` so a
  // registered falsy value (defensive — handlers should be functions, but
  // a Map.get returning undefined is the only fall-through trigger).
  var registered = window.__bppActionRegistry && window.__bppActionRegistry.get(name);
  var fn = registered !== undefined ? registered : window[name];
  if (typeof fn !== "function") { console.warn("bpp dispatch: unknown action:", name); return; }
  // data-pass-event: event is first arg; any data-arg* follow after it
  var args = "passEvent" in el.dataset ? [e].concat(_bppCollectArgs(el)) : _bppCollectArgs(el);
  fn.apply(el, args);
}, true);
document.addEventListener("input", function _bppInputDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-oninput]");
  if (!el) return;
  var fn = window[el.dataset.oninput];
  if (typeof fn !== "function") return;
  fn.call(el, el.value, e);
});
document.addEventListener("change", function _bppChangeDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onchange]");
  if (!el) return;
  var fn = window[el.dataset.onchange];
  if (typeof fn !== "function") return;
  fn.call(el, el.value, e);
});
document.addEventListener("keydown", function _bppKeydownDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onkeydown]");
  if (!el) return;
  var fn = window[el.dataset.onkeydown];
  if (typeof fn !== "function") return;
  fn.call(el, e);
});
document.addEventListener("mousedown", function _bppMousedownDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onmousedown]");
  if (!el) return;
  var fn = window[el.dataset.onmousedown];
  if (typeof fn !== "function") return;
  fn.call(el, e);
});
document.addEventListener("mouseup", function _bppMouseupDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onmouseup]");
  if (!el) return;
  var fn = window[el.dataset.onmouseup];
  if (typeof fn !== "function") return;
  fn.call(el, e);
});
document.addEventListener("mouseleave", function _bppMouseleaveDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onmouseleave]");
  if (!el) return;
  var fn = window[el.dataset.onmouseleave];
  if (typeof fn !== "function") return;
  fn.call(el, e);
}, true);
document.addEventListener("touchstart", function _bppTouchstartDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-ontouchstart]");
  if (!el) return;
  var fn = window[el.dataset.ontouchstart];
  if (typeof fn !== "function") return;
  fn.call(el, e);
});
document.addEventListener("touchend", function _bppTouchendDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-ontouchend]");
  if (!el) return;
  var fn = window[el.dataset.ontouchend];
  if (typeof fn !== "function") return;
  fn.call(el, e);
});

// Named helpers for complex inline expressions
window._toggleSettingTip = function() {
  this.closest(".setting-item").classList.toggle("tip-open");
};
window._bppReload = function() { location.reload(); };
window._bppThisSelect = function() { this.select(); };
window._openShareTab = function() { switchSettingsTab("share"); _renderShareTab(); };
window._openActivityTab = function() { switchSettingsTab("activity"); _renderActivityTab(); };

// Settings-panel action wrappers: close the panel then call the real function.
// These replace the old dead data-arg0=");fn(" pattern where the chained call
// was never executed because the dispatcher passes the string as an argument
// to hideSettings() rather than evaluating it.
window._settingsRenameLibrary  = function() { hideSettings(); renameCurrentLibrary(); };
window._settingsSwitchLibrary  = function() { hideSettings(); showLibraryPicker(); };
window._settingsStartTour      = function() { hideSettings(); startTour(); };
window._settingsReanalyze      = function() { hideSettings(); startReanalyze(); };
window._settingsRecomputeHashes = function() { hideSettings(); recomputeHashes(); };
window._settingsAutoOptimize   = function() { hideSettings(); runAutoOptimize(); };

window._nudgeAction = function(fn, id) {
  if (typeof window[fn] === "function") window[fn]();
  if (typeof window.dismissNudge === "function") window.dismissNudge(id);
};
window._setAspectRatioFromEl = function(val) {
  if (typeof window._setAspectRatio === "function") window._setAspectRatio(val, this);
};
window._kbEnterCreateAlbum = function(e) {
  if (e.key === "Enter" && typeof window.createAlbumAndAdd === "function") window.createAlbumAndAdd();
};
window._kbEnterCustomRatio = function(e) {
  if (e.key === "Enter" && typeof window._applyCustomRatio === "function") window._applyCustomRatio();
};
window._kbToggleTip = function(e) {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    var item = this.closest(".setting-item");
    if (item) item.classList.toggle("tip-open");
  }
};

document.addEventListener("contextmenu", function _bppContextmenuDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-oncontextmenu]");
  if (!el) return;
  var fn = window[el.dataset.oncontextmenu];
  if (typeof fn !== "function") return;
  // Contextmenu handlers always receive event first, then any data-arg* values
  fn.apply(el, [e].concat(_bppCollectArgs(el)));
}, true);
document.addEventListener("dblclick", function _bppDblclickDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-ondblclick]");
  if (!el) return;
  var fn = window[el.dataset.ondblclick];
  if (typeof fn !== "function") return;
  fn.apply(el, [e].concat(_bppCollectArgs(el)));
}, true);
document.addEventListener("toggle", function _bppToggleDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = e.target.closest ? e.target : null;
  if (!el || !el.dataset || !el.dataset.ontoggle) return;
  var fn = window[el.dataset.ontoggle];
  if (typeof fn !== "function") return;
  fn.call(el, e);
}, true);
// Named helpers for complex contextmenu expressions
window._bppAlbumCtxMenu = function(e) {
  var el = this.closest("[data-oncontextmenu]") || this;
  var cid = el.dataset.cid != null ? +el.dataset.cid : null;
  var aid = +el.dataset.arg0;
  var name = el.dataset.arg1 || "";
  if (cid != null) window.showPersonCtxMenu ? window.showPersonCtxMenu(e, cid) : window.showSmartAlbumMenu(e, aid, name);
  else window.showSmartAlbumMenu && window.showSmartAlbumMenu(e, aid, name);
};
window._bppDblclickToggle = function(e) {
  e.preventDefault(); e.stopPropagation();
  this.parentElement.toggleAttribute("open");
};

document.addEventListener("load", function _bppLoadDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = e.target.closest ? e.target : null;
  if (!el || !el.dataset) return;
  var fn = el.dataset.onload ? window[el.dataset.onload] : null;
  if (typeof fn === "function") fn.call(el, e);
}, true);
document.addEventListener("error", function _bppErrorDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = e.target.closest ? e.target : null;
  if (!el || !el.dataset) return;
  var fn = el.dataset.onerror ? window[el.dataset.onerror] : null;
  if (typeof fn === "function") fn.call(el, e);
}, true);
window._bppThumbLoaded = function() { this.parentElement.classList.remove("thumb-loading"); };
window._bppThumbBroken = function() { this.parentElement.classList.add("lb-thumb-broken"); };

document.addEventListener("pointerdown", function _bppPointerdownDispatch(e) { if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  var el = _t.closest("[data-onpointerdown]");
  if (!el) return;
  var fn = window[el.dataset.onpointerdown];
  if (typeof fn !== "function") return;
  fn.call(el, e);
}, true);
window._personCtxMenuDispatch = function(e) {
  e.preventDefault();
  if (typeof window.showPersonCtxMenu === "function")
    window.showPersonCtxMenu(e, +this.dataset.arg0);
};
window._personPointerDownDispatch = function(e) {
  if (typeof window._personPointerDown === "function")
    window._personPointerDown.call(this, e, +this.dataset.arg0);
};

window._bppCardCtxMenu = function(e) {
  if (typeof window.showCardCtxMenu === "function")
    window.showCardCtxMenu(e, this.dataset.filepath || "");
};

// Generic backdrop-close: fires the named function only if click was directly on the overlay
window._bppBackdropClose = function(e) {
  if (!e || !e.target) return; var _t = typeof e.target.closest === "function" ? e.target : (e.target.parentElement || null); if (!_t) return;
  if (e.target !== this) return;
  var fn = this.dataset.backdropFn;
  var arg = this.dataset.backdropArg;
  if (typeof window[fn] === "function") {
    window[fn](arg !== undefined ? _bppCoerceArg(arg) : undefined);
  }
};
