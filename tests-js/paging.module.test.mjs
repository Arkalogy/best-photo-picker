// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(),
  authedSrc: (/** @type {string} */ u) => u,
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast: vi.fn(), toastError: vi.fn() }));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { fetchAllPages } from "../bpp/web/static/js/modules/paging.mjs";

afterEach(() => {
  vi.mocked(apiFetch).mockReset();
});

describe("fetchAllPages", () => {
  test("walks limit/offset pages and hands each page to the consumer", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ photos: [1, 2], has_more: true })
      .mockResolvedValueOnce({ photos: [3], has_more: false });
    const pages = [];
    const all = await fetchAllPages("/api/v1/tags/1/photos", {
      pageSize: 2,
      key: "photos",
      onPage: (p) => pages.push(p),
    });
    expect(all).toEqual([1, 2, 3]);
    expect(pages).toEqual([[1, 2], [3]]);
    const urls = vi.mocked(apiFetch).mock.calls.map((c) => c[0]);
    expect(urls[0]).toContain("limit=2&offset=0");
    expect(urls[1]).toContain("limit=2&offset=2");
  });

  test("stops when the view changed", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ photos: [1], has_more: true });
    let alive = true;
    const all = await fetchAllPages("/x?a=1", {
      pageSize: 1,
      key: "photos",
      stillWanted: () => alive,
      onPage: () => {
        alive = false;
      },
    });
    expect(all).toEqual([1]);
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(1);
  });
});

describe("fetchAllPages after the view changed", () => {
  test("a page that lands after the user left is not delivered", async () => {
    let wanted = true;
    vi.mocked(apiFetch).mockImplementation(async () => {
      wanted = false; // the user switches views while this page is in flight
      return { photos: [9], has_more: true };
    });
    const pages = [];
    const all = await fetchAllPages("/x", {
      pageSize: 1,
      key: "photos",
      stillWanted: () => wanted,
      onPage: (p) => pages.push(p),
    });
    expect(pages).toEqual([]);
    expect(all).toEqual([]);
  });
});
