// @ts-check
/**
 * Activity log: notification bell in the status bar + Settings →
 * Activity tab + the 30s polling worker that drives both.
 *
 * Reads `/api/v1/logs` and `/api/v1/logs/clear`. Maintains:
 *  - in-memory entries list
 *  - "last seen" timestamp in localStorage
 *  - polling timer
 *  - dropdown open state
 *  - "is this the initial load" flag (suppresses the new-warning toast
 *    on first fetch so we don't spam users on app boot)
 */

import { humanizeActivityList } from "./activity-humanize.mjs";
import { apiFetch } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { showToast, toast, toastError } from "./toast.mjs";

/**
 * @typedef {Object} ActivityEntry
 * @property {number} ts - Unix seconds.
 * @property {string} level - "INFO" | "WARNING" | "ERROR" | etc.
 * @property {string} [msg]
 */

/** @type {ActivityEntry[]} */
let _activityEntries = [];
let _activityLastSeen = 0;
/** @type {ReturnType<typeof setInterval> | null} */
let _activityPollTimer = null;
let _activityDropdownOpen = false;
let _activityInitialLoad = false;

/** Hydrate the persisted "last seen" stamp lazily — localStorage may not be available at module-load. */
function _hydrateLastSeen() {
  if (_activityLastSeen !== 0) return;
  try {
    _activityLastSeen = parseFloat(localStorage.getItem("activityLastSeen") || "0") || 0;
  } catch {
    _activityLastSeen = 0;
  }
}

/** Test-only: reset module state between tests. */
export function _resetActivityState() {
  _activityEntries = [];
  _activityLastSeen = 0;
  _activityPollTimer = null;
  _activityDropdownOpen = false;
  _activityInitialLoad = false;
}

export function _getActivityEntries() {
  return _activityEntries;
}

/** @param {ActivityEntry[]} entries */
export function _setActivityEntries(entries) {
  _activityEntries = entries;
}

/**
 * Format a unix-seconds timestamp for the dropdown — same-day shows
 * just HH:MM, otherwise MM/DD HH:MM.
 *
 * @param {number} unix
 */
export function _formatActivityTs(unix) {
  if (!unix) return "";
  const d = new Date(unix * 1000);
  const now = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) return hh + ":" + mm;
  const mon = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return mon + "/" + day + " " + hh + ":" + mm;
}

/**
 * Count of unseen WARNING/ERROR entries — drives the bell badge.
 *
 * Counts only entries that survive humanization (i.e. ones the user
 * actually sees in the friendly feed). A purely-technical warning that's
 * hidden from the dropdown must not ping the bell.
 */
export function _activityBadgeCount() {
  _hydrateLastSeen();
  let count = 0;
  for (const e of humanizeActivityList(_activityEntries)) {
    if (e.ts > _activityLastSeen && (e.level === "WARNING" || e.level === "ERROR")) {
      count++;
    }
  }
  return count;
}

export function _updateBellBadge() {
  const badge = /** @type {HTMLElement | null} */ (document.getElementById("activity-badge"));
  if (!badge) return;
  const count = _activityBadgeCount();
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.style.display = count > 0 ? "flex" : "none";
}

/**
 * Render the bell-dropdown list — the user-friendly feed (last 20, newest
 * first). Raw log lines are run through the humanizer: technical plumbing
 * is dropped, milestones become plain language. The full technical log
 * lives in the Settings → Activity tab (`_renderActivityTab`).
 */
export function _renderDropdown() {
  const list = document.getElementById("activity-dropdown-list");
  if (!list) return;
  list.innerHTML = "";

  // Humanize the whole buffer first, THEN take the last 20 — taking 20 raw
  // lines first would mostly yield hidden plumbing and leave the feed empty.
  const recent = humanizeActivityList(_activityEntries).slice(-20).reverse();
  if (recent.length === 0) {
    list.innerHTML = '<div class="activity-empty">No recent activity</div>';
    return;
  }

  for (const e of recent) {
    const div = document.createElement("div");
    div.className =
      "activity-item" +
      (e.level === "ERROR" ? " error" : e.level === "WARNING" ? " warning" : "");
    const levelSpan = document.createElement("span");
    levelSpan.className = "activity-level";
    levelSpan.textContent =
      e.level === "ERROR" ? "⚠" : e.level === "WARNING" ? "△" : "•";
    const tsSpan = document.createElement("span");
    tsSpan.className = "activity-ts";
    tsSpan.textContent = _formatActivityTs(e.ts);
    const msgSpan = document.createElement("span");
    msgSpan.className = "activity-msg";
    msgSpan.textContent = e.text;
    div.appendChild(levelSpan);
    div.appendChild(tsSpan);
    div.appendChild(msgSpan);
    list.appendChild(div);
  }
}

export function toggleActivityDropdown() {
  const dropdown = /** @type {HTMLElement | null} */ (
    document.getElementById("activity-dropdown")
  );
  if (!dropdown) return;
  _activityDropdownOpen = !_activityDropdownOpen;
  dropdown.style.display = _activityDropdownOpen ? "block" : "none";
  if (_activityDropdownOpen) {
    _renderDropdown();
    _fetchActivityEntries();
    _activityLastSeen = Date.now() / 1000;
    try {
      localStorage.setItem("activityLastSeen", String(_activityLastSeen));
    } catch {
      /* localStorage may be unavailable in jsdom shims */
    }
    _updateBellBadge();
  }
}

/** @param {MouseEvent} e */
export function _closeActivityDropdown(e) {
  if (!_activityDropdownOpen) return;
  const bell = document.getElementById("activity-bell-wrap");
  const target = /** @type {Node} */ (e.target);
  if (bell && bell.contains(target)) return;
  const dropdown = /** @type {HTMLElement | null} */ (
    document.getElementById("activity-dropdown")
  );
  if (dropdown && dropdown.contains(target)) return;
  _activityDropdownOpen = false;
  if (dropdown) dropdown.style.display = "none";
}

/** Render the Settings → Activity tab — supports level-filter + date prefix. */
export function _renderActivityTab() {
  const container = document.getElementById("activity-log-entries");
  if (!container) return;
  const filterEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("activity-level-filter")
  );
  const minLevel = filterEl ? filterEl.value : "all";
  container.innerHTML = "";

  let filtered = _activityEntries;
  if (minLevel === "warning") {
    filtered = filtered.filter((e) => e.level === "WARNING" || e.level === "ERROR");
  } else if (minLevel === "error") {
    filtered = filtered.filter((e) => e.level === "ERROR");
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="activity-empty">No log entries</div>';
    return;
  }

  for (let i = filtered.length - 1; i >= 0; i--) {
    const e = filtered[i];
    const div = document.createElement("div");
    div.className =
      "activity-log-line" +
      (e.level === "ERROR" ? " error" : e.level === "WARNING" ? " warning" : "");
    let msg = e.msg || "";
    if (e.ts) {
      const d = new Date(e.ts * 1000);
      if (d.toDateString() !== new Date().toDateString()) {
        const mon = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        msg = mon + "/" + day + " " + msg;
      }
    }
    div.textContent = msg;
    container.appendChild(div);
  }
}

export function activityFilterChanged() {
  _renderActivityTab();
}

export async function copyActivityLog() {
  // Respect whatever the user has filtered to in the dropdown —
  // grabbing 1000 lines they explicitly hid would defeat the filter.
  const filterEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("activity-level-filter")
  );
  const minLevel = filterEl ? filterEl.value : "all";
  let entries = _activityEntries;
  if (minLevel === "warning") {
    entries = entries.filter((e) => e.level === "WARNING" || e.level === "ERROR");
  } else if (minLevel === "error") {
    entries = entries.filter((e) => e.level === "ERROR");
  }
  if (entries.length === 0) {
    toast("No log entries to copy", true);
    return;
  }
  const text = entries
    .map((e) => /** @type {any} */ (e).msg || "")
    .filter(Boolean)
    .join("\n");
  try {
    await navigator.clipboard.writeText(text);
    toast(`Copied ${entries.length} log line${entries.length === 1 ? "" : "s"}`);
  } catch (err) {
    // Some embedded WebViews refuse clipboard.writeText without an
    // explicit permission grant. Fall back to a textarea + execCommand
    // so the user still gets something rather than a silent failure.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      toast(`Copied ${entries.length} log line${entries.length === 1 ? "" : "s"}`);
    } catch (e) {
      toast("Couldn't copy to clipboard", true);
    } finally {
      ta.remove();
    }
  }
}

export async function clearActivityLog() {
  const ok = await appConfirm(
    "Clear activity log?",
    "This will erase the server log file. This cannot be undone.",
  );
  if (!ok) return;
  apiFetch("/api/v1/logs/clear", { method: "POST" })
    .then(() => {
      _activityEntries = [];
      _renderActivityTab();
      _updateBellBadge();
      toast("Activity log cleared");
    })
    .catch((e) => {
      console.warn("Clear activity log failed:", e);
      toastError("clear the activity log", e);
    });
}

/** Refresh entries from the server, update bell + open dropdown / pane. */
export function _fetchActivityEntries() {
  const prevCount = _activityBadgeCount();
  apiFetch("/api/v1/logs?limit=500")
    .then((data) => {
      if (data && data.entries) {
        _activityEntries = data.entries;
        _updateBellBadge();
        if (_activityDropdownOpen) {
          _renderDropdown();
        }
        const pane = document.getElementById("settings-pane-activity");
        if (pane && pane.classList.contains("active")) {
          _renderActivityTab();
        }
        if (_activityInitialLoad) {
          const newCount = _activityBadgeCount();
          if (newCount > prevCount && !_activityDropdownOpen) {
            const diff = newCount - prevCount;
            // Bug #8 (UAT 2026-06-01): the legacy showToast(msg, ms, cb)
            // shape labels the action button 'Undo' regardless of what
            // the callback actually does — clicking it OPENED the
            // activity log, which looked nonsensical next to a warning.
            // Use the typed toast() with an explicit 'View' action.
            toast(diff + " new warning" + (diff > 1 ? "s" : ""), "warning", {
              action: { label: "View", fn: showActivityLog },
            });
          }
        }
        _activityInitialLoad = true;
      }
    })
    .catch((e) => {
      // One miss is harmless — next poll covers it — but a persistent
      // failure (server down, auth rotated, network broken) would
      // freeze the Activity feed with no indication. Console.warn so
      // it's visible in DevTools without spamming the user with toasts.
      console.warn("Activity poll failed:", e);
    });
}

export function startActivityPolling() {
  _fetchActivityEntries();
  if (_activityPollTimer) clearInterval(_activityPollTimer);
  _activityPollTimer = setInterval(_fetchActivityEntries, 30000);
}

export function showActivityLog() {
  /** @type {any} */
  const win = window;
  win.showSettings?.();
  win.switchSettingsTab?.("activity");
  _renderActivityTab();
}

export function initActivityLog() {
  document.addEventListener("click", _closeActivityDropdown);
  startActivityPolling();
}
