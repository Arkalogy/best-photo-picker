// @ts-check
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));

const { downloadProgressText, waitForDownloadJob } =
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
