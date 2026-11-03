// @ts-check
/**
 * The Browse buttons go through pickFolder(): off macOS (the Docker
 * image) the server has no picker, and the user must hear why nothing
 * opened.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn();
const toastMock = vi.fn();

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({ apiFetch: apiFetchMock }));
const toastErrorMock = vi.fn();
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: toastMock,
  toastError: toastErrorMock,
}));

const { pickFolder, NO_PICKER_MESSAGE, NO_PICKER_LIBRARY_MESSAGE } =
  await import("../bpp/web/static/js/modules/native-pick.mjs");

beforeEach(() => {
  apiFetchMock.mockReset();
  toastMock.mockReset();
  toastErrorMock.mockReset();
});

describe("pickFolder", () => {
  test("returns the chosen path", async () => {
    apiFetchMock.mockResolvedValueOnce({ path: "/photos" });
    expect(await pickFolder()).toBe("/photos");
    expect(toastMock).not.toHaveBeenCalled();
  });

  test("a cancel returns an empty path quietly", async () => {
    apiFetchMock.mockResolvedValueOnce({ cancelled: true });
    expect(await pickFolder()).toBe("");
    expect(toastMock).not.toHaveBeenCalled();
  });

  test("no picker on this server: says to type the path", async () => {
    apiFetchMock.mockResolvedValueOnce({ cancelled: true, unsupported: true });
    expect(await pickFolder()).toBe("");
    expect(toastMock).toHaveBeenCalledWith(NO_PICKER_MESSAGE, "warning");
  });

  test("a caller with no path box passes its own hint", async () => {
    apiFetchMock.mockResolvedValueOnce({ cancelled: true, unsupported: true });
    await pickFolder(NO_PICKER_LIBRARY_MESSAGE);
    expect(toastMock).toHaveBeenCalledWith(NO_PICKER_LIBRARY_MESSAGE, "warning");
  });

  test("a failed request names the action and returns no path", async () => {
    const err = new Error("HTTP 500");
    apiFetchMock.mockRejectedValueOnce(err);
    expect(await pickFolder()).toBe("");
    expect(toastErrorMock).toHaveBeenCalledWith("open the folder browser", err);
  });
});
