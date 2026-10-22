// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ u) => u,
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast: vi.fn(), toastError: vi.fn() }));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { toast } from "../bpp/web/static/js/modules/toast.mjs";
import {
  RESUME_TEXT,
  monitorBootHealth,
  planBootHealth,
} from "../bpp/web/static/js/modules/boot-health.mjs";

const fresh = () => ({ resumeShown: false, resumeFailedTold: false, refusedTold: false });

describe("planBootHealth", () => {
  test("a resume in flight shows the status once and keeps polling", () => {
    const plan = planBootHealth({ checks: { face_recovery: { in_flight: true } } }, fresh());
    expect(plan).toEqual({ showResume: true, hideResume: false, toasts: [], keepPolling: true });
    const again = planBootHealth(
      { checks: { face_recovery: { in_flight: true } } },
      { ...fresh(), resumeShown: true }
    );
    expect(again.showResume).toBe(false);
    expect(again.keepPolling).toBe(true);
  });

  test("a finished resume hides the status and stops polling", () => {
    const plan = planBootHealth(
      { checks: { face_recovery: { in_flight: false } } },
      { ...fresh(), resumeShown: true }
    );
    expect(plan.hideResume).toBe(true);
    expect(plan.keepPolling).toBe(false);
  });

  test("a failed resume and a refused scan each get one toast with a next step", () => {
    const health = {
      checks: {
        face_recovery: { in_flight: false, failed: true },
        missing_scan: { refused: true, missing: 1200, total: 2000 },
      },
    };
    const plan = planBootHealth(health, fresh());
    expect(plan.toasts).toHaveLength(2);
    expect(plan.toasts[0]).toMatch(/Re-extract faces/);
    expect(plan.toasts[1]).toMatch(/1200 of 2000 photos couldn't be found/);
    const told = planBootHealth(health, { ...fresh(), resumeFailedTold: true, refusedTold: true });
    expect(told.toasts).toEqual([]);
  });

  test("a clean library needs nothing", () => {
    const plan = planBootHealth({ checks: {} }, fresh());
    expect(plan).toEqual({ showResume: false, hideResume: false, toasts: [], keepPolling: false });
    expect(planBootHealth(null, fresh()).keepPolling).toBe(false);
  });
});

describe("monitorBootHealth", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = `
      <div id="status-bar">
        <div id="status-progress" class="hidden">
          <span id="status-progress-text"></span>
          <div id="status-progress-fill"></div>
        </div>
      </div>`;
    /** @type {any} */ (window).show = vi.fn();
    /** @type {any} */ (window).activeOperation = null;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.mocked(apiFetch).mockReset();
    vi.mocked(toast).mockReset();
  });

  test("mirrors the resume into the status bar, then clears it and stops", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ checks: { face_recovery: { in_flight: true } } })
      .mockResolvedValueOnce({ checks: { face_recovery: { in_flight: false } } });
    const run = monitorBootHealth();
    await vi.advanceTimersByTimeAsync(0);
    const text = document.getElementById("status-progress-text");
    expect(text?.textContent).toBe(RESUME_TEXT);
    expect(document.getElementById("status-progress")?.classList.contains("hidden")).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);
    await run;
    expect(document.getElementById("status-progress")?.classList.contains("hidden")).toBe(true);
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(2);
  });

  test("a refused scan is one toast, and a dead server ends the poll quietly", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      checks: { missing_scan: { refused: true, missing: 3, total: 4 } },
    });
    await monitorBootHealth();
    expect(vi.mocked(toast)).toHaveBeenCalledTimes(1);
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("down"));
    await monitorBootHealth();
    expect(vi.mocked(toast)).toHaveBeenCalledTimes(1);
  });
});
