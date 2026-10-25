// @ts-check
/**
 * The install confirms name the packages that are about to be installed
 * (from /api/v1/install/<key>/info), so the consent is informed.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));

const { installPackagesNote } = await import("../bpp/web/static/js/modules/analysis-install.mjs");

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe("installPackagesNote", () => {
  test("lists the packages and where they come from", async () => {
    apiFetchMock.mockResolvedValueOnce({
      key: "faces",
      packages: ["face_recognition>=1.3", "dlib>=19"],
      host: "pypi.org",
    });
    const note = await installPackagesNote("faces");
    expect(apiFetchMock.mock.calls[0][0]).toBe("/api/v1/install/faces/info");
    expect(apiFetchMock.mock.calls[0][1]?.signal).toBeTruthy();
    expect(note).toContain("face_recognition>=1.3");
    expect(note).toContain("dlib>=19");
    expect(note).toContain("pypi.org");
  });

  test("an unreachable info endpoint does not block the confirm", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("HTTP 503"));
    expect(await installPackagesNote("inpaint")).toBe("");
  });
});
