// @ts-check
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { monitorPhashBackfill } from "../bpp/web/static/js/modules/phash-status.mjs";

function jsonResp(body) {
  return { ok: true, status: 200, json: async () => body };
}

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="status-bar">
      <div id="status-progress" class="hidden">
        <span id="status-progress-text"></span>
        <div id="status-progress-fill"></div>
      </div>
    </div>`;
  const meta = document.createElement("meta");
  meta.setAttribute("name", "auth-token");
  meta.setAttribute("content", "t");
  document.head.appendChild(meta);
  /** @type {any} */ (window).show = vi.fn();
  /** @type {any} */ (window).activeOperation = null;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.head.innerHTML = "";
});

test("mirrors backfill progress into the status bar, then hides on completion", async () => {
  const seq = [
    { phash_progress: { running: true, done: 3, total: 10 } },
    { phash_progress: { running: true, done: 7, total: 10 } },
    { phash_progress: { running: false, done: 10, total: 10 } },
  ];
  let i = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("/api/v1/status")) {
        return jsonResp(seq[Math.min(i++, seq.length - 1)]);
      }
      return jsonResp({}); // refreshSmartAlbums etc. — harmless
    })
  );

  const progress = document.getElementById("status-progress");
  const textEl = document.getElementById("status-progress-text");

  const finished = monitorPhashBackfill();

  // First tick fires immediately: running 3/10 → bar visible with text.
  await vi.advanceTimersByTimeAsync(0);
  expect(progress?.classList.contains("hidden")).toBe(false);
  expect(textEl?.textContent).toContain("Computing photo similarity 3/10");

  // Second tick: 7/10.
  await vi.advanceTimersByTimeAsync(2000);
  expect(textEl?.textContent).toContain("7/10");

  // Third tick: running=false → bar hidden, poll stops.
  await vi.advanceTimersByTimeAsync(2000);
  await finished;
  expect(progress?.classList.contains("hidden")).toBe(true);
});

test("stays silent + stops when no backfill is running", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("/api/v1/status")) {
        return jsonResp({ phash_progress: { running: false, done: 0, total: 0 } });
      }
      return jsonResp({});
    })
  );
  const progress = document.getElementById("status-progress");
  const finished = monitorPhashBackfill();
  // Drive past the idle-tick budget.
  for (let k = 0; k < 7; k++) await vi.advanceTimersByTimeAsync(2000);
  await finished;
  // Never shown.
  expect(progress?.classList.contains("hidden")).toBe(true);
});
