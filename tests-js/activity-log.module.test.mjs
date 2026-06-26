// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _activityBadgeCount,
  _formatActivityTs,
  _renderActivityTab,
  _renderDropdown,
  _resetActivityState,
  _setActivityEntries,
  _updateBellBadge,
  toggleActivityDropdown,
} from "../bpp/web/static/js/modules/activity-log.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="activity-bell-wrap">
      <span id="activity-badge" style="display:none">0</span>
    </div>
    <div id="activity-dropdown" style="display:none">
      <div id="activity-dropdown-list"></div>
    </div>
    <div id="settings-pane-activity">
      <select id="activity-level-filter"><option value="all" selected></option></select>
      <div id="activity-log-entries"></div>
    </div>
  `;
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
  _resetActivityState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const badge = () => /** @type {HTMLElement} */ (document.getElementById("activity-badge"));
const dropdown = () => /** @type {HTMLElement} */ (document.getElementById("activity-dropdown"));

describe("_formatActivityTs", () => {
  test("empty for falsy timestamp", () => {
    expect(_formatActivityTs(0)).toBe("");
  });

  test("same-day: HH:MM only", () => {
    const now = new Date();
    const sameDay =
      new Date(now.getFullYear(), now.getMonth(), now.getDate(), 14, 7).getTime() / 1000;
    expect(_formatActivityTs(sameDay)).toBe("14:07");
  });

  test("different day: MM/DD HH:MM", () => {
    const fixed = new Date(2020, 5, 15, 9, 5).getTime() / 1000; // June 15
    expect(_formatActivityTs(fixed)).toBe("06/15 09:05");
  });
});

describe("_activityBadgeCount", () => {
  test("zero for empty entries", () => {
    expect(_activityBadgeCount()).toBe(0);
  });

  test("counts only humanized WARNING/ERROR with ts > lastSeen", () => {
    const now = Date.now() / 1000;
    _setActivityEntries([
      { ts: now - 100, level: "INFO", msg: "Analysis started" }, // visible INFO, not counted
      { ts: now - 100, level: "WARNING", msg: "Pet model download failed, pet detection disabled" },
      { ts: now - 100, level: "ERROR", msg: "boom" }, // unmatched ERROR -> generic, counted
      { ts: now + 100, level: "WARNING", msg: "pet detection disabled" },
    ]);
    expect(_activityBadgeCount()).toBe(3);
  });

  test("hidden (plumbing) warnings do not count toward the badge", () => {
    const now = Date.now() / 1000;
    _setActivityEntries([
      { ts: now - 100, level: "WARNING", msg: "Update check failed: repo not found" },
      { ts: now - 100, level: "WARNING", msg: "Recovery: no live ctx; leaving journal row" },
      { ts: now - 100, level: "WARNING", msg: "SHA-256 backfill failed" },
    ]);
    expect(_activityBadgeCount()).toBe(0);
  });

  test("filters by lastSeen via localStorage", () => {
    const cutoff = 1000;
    localStorage.setItem("activityLastSeen", String(cutoff));
    _setActivityEntries([
      { ts: 500, level: "WARNING", msg: "pet detection disabled" }, // before cutoff
      { ts: 2000, level: "WARNING", msg: "pet detection disabled" }, // after
      { ts: 2000, level: "INFO", msg: "Analysis started" }, // not warning
    ]);
    expect(_activityBadgeCount()).toBe(1);
  });
});

describe("_updateBellBadge", () => {
  test("hides when count is 0", () => {
    _setActivityEntries([]);
    _updateBellBadge();
    expect(badge().style.display).toBe("none");
  });

  test("shows count when warnings exist", () => {
    const future = Date.now() / 1000 + 100;
    _setActivityEntries([
      { ts: future, level: "WARNING", msg: "pet detection disabled" },
      { ts: future, level: "ERROR", msg: "boom" },
    ]);
    _updateBellBadge();
    expect(badge().textContent).toBe("2");
    expect(badge().style.display).toBe("flex");
  });

  test("'99+' for >99 entries", () => {
    const future = Date.now() / 1000 + 100;
    _setActivityEntries(
      Array.from({ length: 105 }, () => ({
        ts: future,
        level: "WARNING",
        msg: "pet detection disabled",
      }))
    );
    _updateBellBadge();
    expect(badge().textContent).toBe("99+");
  });
});

describe("_renderDropdown", () => {
  test("'No recent activity' for empty entries", () => {
    _setActivityEntries([]);
    _renderDropdown();
    expect(document.getElementById("activity-dropdown-list").textContent).toContain(
      "No recent activity"
    );
  });

  test("renders one .activity-item per humanized entry, newest-first, capped at 20", () => {
    _setActivityEntries(
      Array.from({ length: 25 }, (_, i) => ({
        ts: 1000 + i,
        level: "INFO",
        msg: `Import complete: ${i} imported, 0 skipped, 0 errors`,
      }))
    );
    _renderDropdown();
    const items = document.querySelectorAll(".activity-item");
    expect(items).toHaveLength(20);
    // First item is the highest-ts (newest)
    expect(items[0].querySelector(".activity-msg")?.textContent).toBe("Imported 24 photos");
  });

  test("hides technical plumbing lines, shows only humanized milestones", () => {
    _setActivityEntries([
      { ts: 1000, level: "INFO", msg: "12:34:56 [INFO ] bpp.web.base_worker: Analysis started" },
      {
        ts: 1001,
        level: "INFO",
        msg: "Phase 'scoring' done (pid=89168, exitcode=0, crashed=False)",
      },
      { ts: 1002, level: "INFO", msg: "Starting SHA-256 backfill thread" },
    ]);
    _renderDropdown();
    const items = document.querySelectorAll(".activity-item");
    expect(items).toHaveLength(1);
    expect(items[0].querySelector(".activity-msg")?.textContent).toBe("Analyzing your photos…");
  });
});

describe("toggleActivityDropdown", () => {
  test("opens + renders + persists lastSeen + clears badge", () => {
    const future = Date.now() / 1000 + 100;
    _setActivityEntries([{ ts: future, level: "WARNING", msg: "pet detection disabled" }]);
    _updateBellBadge();
    expect(badge().style.display).toBe("flex"); // 1 unseen warning

    // Stub apiFetch to no-op
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ entries: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    toggleActivityDropdown();
    expect(dropdown().style.display).toBe("block");
    expect(localStorage.getItem("activityLastSeen")).toBeTruthy();
  });

  test("second toggle closes", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 }))
    );
    toggleActivityDropdown();
    toggleActivityDropdown();
    expect(dropdown().style.display).toBe("none");
  });
});

describe("_renderActivityTab", () => {
  test("'No log entries' on empty", () => {
    _setActivityEntries([]);
    _renderActivityTab();
    expect(document.getElementById("activity-log-entries").textContent).toContain("No log entries");
  });

  test("renders newest-first with .error / .warning class", () => {
    _setActivityEntries([
      { ts: 1000, level: "INFO", msg: "first" },
      { ts: 2000, level: "WARNING", msg: "warn" },
      { ts: 3000, level: "ERROR", msg: "err" },
    ]);
    _renderActivityTab();
    const lines = document.querySelectorAll(".activity-log-line");
    expect(lines).toHaveLength(3);
    // Newest first
    expect(lines[0].textContent).toContain("err");
    expect(lines[0].classList.contains("error")).toBe(true);
    expect(lines[1].classList.contains("warning")).toBe(true);
  });

  test("filter=warning hides INFO entries", () => {
    /** @type {HTMLSelectElement} */ (document.getElementById("activity-level-filter")).innerHTML =
      '<option value="warning" selected></option>';
    _setActivityEntries([
      { ts: 1000, level: "INFO", msg: "i" },
      { ts: 2000, level: "WARNING", msg: "w" },
      { ts: 3000, level: "ERROR", msg: "e" },
    ]);
    _renderActivityTab();
    expect(document.querySelectorAll(".activity-log-line")).toHaveLength(2);
  });

  test("filter=error hides WARNING + INFO entries", () => {
    /** @type {HTMLSelectElement} */ (document.getElementById("activity-level-filter")).innerHTML =
      '<option value="error" selected></option>';
    _setActivityEntries([
      { ts: 1000, level: "INFO", msg: "i" },
      { ts: 2000, level: "WARNING", msg: "w" },
      { ts: 3000, level: "ERROR", msg: "e" },
    ]);
    _renderActivityTab();
    expect(document.querySelectorAll(".activity-log-line")).toHaveLength(1);
  });
});
