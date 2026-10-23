// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { batchEnhance } from "../bpp/web/static/js/modules/deleted.mjs";
import { setup, teardown, jsonResp } from "./_deleted-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("batchEnhance", () => {
  test("noop on empty selection", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchEnhance();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
