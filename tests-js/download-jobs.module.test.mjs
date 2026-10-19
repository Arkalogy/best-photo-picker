// @ts-check
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));

const { DOWNLOAD_STALLED_TEXT, downloadProgressText, waitForDownloadJob } =
  await import("../bpp/web/static/js/modules/download-jobs.mjs");

const noSleep = () => Promise.resolve();

beforeEach(() => apiFetchMock.mockReset());

describe("downloadProgressText", () => {
  test("percent when the size is known", () => {
    expect(downloadProgressText({ bytes_done: 45, bytes_total: 100 })).toBe("Downloading… 45%");
  });
  test("megabytes when the size is unknown", () => {
    expect(downloadProgressText({ bytes_done: 12.3 * 1024 * 1024, bytes_total: null })).toBe(
      "Downloading… 12.3 MB"
    );
  });
  test("every byte in: finishing (checking the files), not downloading", () => {
    expect(downloadProgressText({ phase: "finishing", bytes_done: 100, bytes_total: 100 })).toBe(
      "Finishing… (checking the download)"
    );
  });
  test("plain text before any bytes arrive", () => {
    expect(downloadProgressText({ bytes_done: 0, bytes_total: null })).toBe("Downloading…");
  });
});

describe("waitForDownloadJob", () => {
  test("polls until done, reporting progress, and resolves with the result", async () => {
    apiFetchMock
      .mockResolvedValueOnce({ status: "running", bytes_done: 10, bytes_total: 100 })
      .mockResolvedValueOnce({ status: "running", bytes_done: 50, bytes_total: 100 })
      .mockResolvedValueOnce({
        status: "done",
        bytes_done: 100,
        bytes_total: 100,
        result: { ok: true },
      });
    const seen = [];
    const snap = await waitForDownloadJob("model:CLIP visual", {
      onProgress: (s) => seen.push(s.bytes_done),
      sleep: noSleep,
    });
    expect(snap.result).toEqual({ ok: true });
    expect(seen).toEqual([10, 50]);
    expect(apiFetchMock.mock.calls[0][0]).toBe(
      "/api/v1/models/download-jobs/" + encodeURIComponent("model:CLIP visual")
    );
  });

  test("rejects with the server's reason when the job fails", async () => {
    apiFetchMock.mockResolvedValueOnce({ status: "error", error: "Couldn't download X: reset" });
    await expect(waitForDownloadJob("k", { sleep: noSleep })).rejects.toThrow(
      "Couldn't download X: reset"
    );
  });
});

describe("waitForDownloadJob — no job key", () => {
  test("rejects when the start response carried no job", async () => {
    await expect(waitForDownloadJob(undefined, { sleep: noSleep })).rejects.toThrow(
      "did not start a download"
    );
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});

describe("a download that stops making progress", () => {
  test("is reported as stalled, never given up on (its files may still be written)", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    apiFetchMock.mockImplementation(async () =>
      ++polls > 10 ? { status: "done" } : { status: "running", bytes_done: 10 }
    );
    const snap = await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 5,
      now: () => (t += 1),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(snap.status).toBe("done"); // waited it out
    expect(stalls).toHaveBeenCalled();
  });

  test("said once per stall, not on every check", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    apiFetchMock.mockImplementation(async () =>
      ++polls > 20 ? { status: "done" } : { status: "running", bytes_done: 10 }
    );
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 3,
      now: () => (t += 1),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(stalls).toHaveBeenCalledTimes(1);
  });

  test("progress resets it", async () => {
    let t = 0;
    let bytes = 0;
    const stalls = vi.fn();
    apiFetchMock.mockImplementation(async () =>
      bytes >= 20 ? { status: "done" } : { status: "running", bytes_done: (bytes += 1) }
    );
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 3,
      now: () => (t += 1),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(stalls).not.toHaveBeenCalled();
  });
});

describe("a job's sign of life keeps the stall clock moving", () => {
  test("a long verification (same bytes, updated_at moving) is not a stall", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    apiFetchMock.mockImplementation(async () =>
      ++polls > 10
        ? { status: "done" }
        : {
            status: "running",
            phase: "finishing",
            bytes_done: 100,
            bytes_total: 100,
            updated_at: polls,
          }
    );
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 5,
      now: () => (t += 1),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(stalls).not.toHaveBeenCalled();
  });

  test("a wait before the first byte reads as starting and stalls only when it stops breathing", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    const seen = /** @type {string[]} */ ([]);
    apiFetchMock.mockImplementation(async () =>
      ++polls > 12
        ? { status: "done" }
        : { status: "running", phase: "starting", bytes_done: 0, updated_at: 1 }
    );
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 5,
      now: () => (t += 1),
      onProgress: (snap) => seen.push(downloadProgressText(snap)),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(seen[0]).toBe("Starting…");
    expect(stalls).toHaveBeenCalledTimes(1);
  });
});

describe("a download whose bytes stop moving for a healthy reason", () => {
  test("every byte in: 'Finishing…' — and a check that hangs is still called stalled", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    const seen = /** @type {string[]} */ ([]);
    apiFetchMock.mockImplementation(async () =>
      ++polls > 10
        ? { status: "done" }
        : {
            status: "running",
            phase: "finishing",
            bytes_done: 100,
            bytes_total: 100,
            updated_at: 1,
          }
    );
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 5,
      now: () => (t += 1),
      onProgress: (snap) => seen.push(downloadProgressText(snap)),
      onStall: stalls,
      sleep: noSleep,
    });
    expect(seen[0]).toBe("Finishing… (checking the download)");
    expect(stalls).toHaveBeenCalled(); // never silent, even here
  });

  test("the stalled label names a way out the desktop app has", () => {
    // (Web and desktop alike; no fixed minutes — the threshold is a setting.)
    expect(DOWNLOAD_STALLED_TEXT).toMatch(/restart Best Photo Picker/);
    expect(DOWNLOAD_STALLED_TEXT).not.toMatch(/reload the page|\d+ minutes/i);
  });
});

describe("a poll that fails", () => {
  /** @param {number} status */
  const httpError = (status) => Object.assign(new Error(`HTTP ${status}`), { status });

  test("once (a blip): the wait goes on — the server's job is still running", async () => {
    apiFetchMock
      .mockRejectedValueOnce(httpError(502))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ status: "done", result: { ok: true } });
    const snap = await waitForDownloadJob("model:x", { sleep: noSleep });
    expect(snap.result).toEqual({ ok: true });
  });

  test("a failed check says so at once (not the last percent, as if all were well)", async () => {
    const retries = vi.fn();
    apiFetchMock
      .mockRejectedValueOnce(httpError(502))
      .mockResolvedValueOnce({ status: "done", result: {} });
    await waitForDownloadJob("model:x", { onRetry: retries, sleep: noSleep });
    expect(retries).toHaveBeenCalledWith(expect.objectContaining({ message: "HTTP 502" }));
  });

  test("an answer that won't change on retry (a 400): the wait ends", async () => {
    apiFetchMock.mockRejectedValueOnce(httpError(400));
    await expect(waitForDownloadJob("model:x", { sleep: noSleep })).rejects.toThrow("HTTP 400");
  });

  test("anything else (a garbled body, a cancelled fetch, a 408): the job may run — retried", async () => {
    apiFetchMock
      .mockRejectedValueOnce(new SyntaxError("Unexpected token"))
      .mockRejectedValueOnce(Object.assign(new Error("aborted"), { name: "AbortError" }))
      .mockRejectedValueOnce(httpError(408))
      .mockResolvedValueOnce({ status: "done", result: { ok: true } });
    const snap = await waitForDownloadJob("model:x", { sleep: noSleep });
    expect(snap.result).toEqual({ ok: true });
  });

  test("unreachable for longer than the stall time: still 'can't reach', not 'stalled'", async () => {
    let t = 0;
    let polls = 0;
    const stalls = vi.fn();
    const retries = vi.fn();
    apiFetchMock.mockImplementation(async () => {
      if (++polls > 10) return { status: "done" };
      throw httpError(503);
    });
    await waitForDownloadJob("model:x", {
      intervalMs: 1,
      stallMs: 3,
      now: () => (t += 1),
      onStall: stalls,
      onRetry: retries,
      sleep: noSleep,
    });
    expect(stalls).not.toHaveBeenCalled();
    expect(retries).toHaveBeenCalledTimes(10);
  });

  test("the server has no such job (it restarted): the wait ends", async () => {
    apiFetchMock.mockRejectedValueOnce(httpError(404));
    await expect(waitForDownloadJob("model:x", { sleep: noSleep })).rejects.toThrow("HTTP 404");
  });
});
