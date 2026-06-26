// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/compare.mjs", () => ({
  _renderCompareSide: vi.fn(),
  closeCompare: vi.fn(),
  setCompareOpen: vi.fn(),
  setCompareSides: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(async () => ({})),
  authedSrc: (/** @type {string} */ s) => s,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn(async () => true),
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: vi.fn(),
  toastError: vi.fn(),
}));

import { apiFetch } from "../bpp/web/static/js/modules/api-client.mjs";
import { toast } from "../bpp/web/static/js/modules/toast.mjs";
import {
  _siblingDelete,
  getSiblings,
  openCompareWithSibling,
} from "../bpp/web/static/js/modules/compare-sibling.mjs";

describe("_siblingDelete — recoverable prune (Item 1)", () => {
  let keeper, s1, s2;
  beforeEach(() => {
    vi.mocked(apiFetch).mockClear();
    vi.mocked(apiFetch).mockResolvedValue({});
    vi.mocked(toast).mockClear();
    keeper = { filepath: "/keeper.jpg", thumb_hash: "hk" };
    s1 = { filepath: "/s1.jpg", filename: "s1.jpg", thumb_hash: "h1" };
    s2 = { filepath: "/s2.jpg", filename: "s2.jpg", thumb_hash: "h2" };
    /** @type {any} */ (window).photos = [keeper, s1, s2];
    /** @type {any} */ (window).renderGrid = vi.fn();
    openCompareWithSibling(keeper, [s1, s2], 0); // focused on s1
  });
  afterEach(() => {
    delete (/** @type {any} */ (window).photos);
    delete (/** @type {any} */ (window).renderGrid);
  });

  const call = (url) => vi.mocked(apiFetch).mock.calls.find((c) => String(c[0]).includes(url));

  test("delete trashes the sibling, marks it deleted in-memory, live-updates the grid", async () => {
    await _siblingDelete();
    const del = call("/photos/delete");
    expect(del).toBeTruthy();
    expect(JSON.parse(del[1].body).filepaths).toEqual(["/s1.jpg"]);
    // in-memory deleted flag → buildMomentStacks/renderGrid drop it from the burst
    expect(s1.deleted_at).toBeTruthy();
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
    // strip dropped the trashed sibling
    expect(getSiblings().map((p) => p.filepath)).toEqual(["/s2.jpg"]);
  });

  test("the toast carries a 20s Undo with the full filename", async () => {
    await _siblingDelete();
    const t = vi.mocked(toast).mock.calls.at(-1);
    expect(t?.[0]).toContain("s1.jpg"); // full copy: Moved "s1.jpg" to trash
    expect(t?.[2]?.duration).toBe(20000);
    expect(t?.[2]?.action?.label).toBe("Undo");
  });

  test("Undo restores the photo and re-inserts it at its original strip index", async () => {
    await _siblingDelete();
    /** @type {any} */ (window).renderGrid.mockClear();
    const undo = vi.mocked(toast).mock.calls.at(-1)?.[2]?.action?.fn;
    expect(undo).toBeTypeOf("function");
    await undo();
    const res = call("/photos/restore");
    expect(res).toBeTruthy();
    expect(JSON.parse(res[1].body).filepaths).toEqual(["/s1.jpg"]);
    expect(s1.deleted_at).toBeFalsy(); // un-trashed in-memory
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled(); // grid live-restored
    // s1 back in the strip at index 0
    expect(getSiblings().map((p) => p.filepath)).toEqual(["/s1.jpg", "/s2.jpg"]);
  });
});
