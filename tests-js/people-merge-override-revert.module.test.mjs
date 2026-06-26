// @ts-check
import { beforeEach, describe, expect, test, vi } from "vitest";

// Side-effect siblings — stub so exclude/include don't repaint or recompute.
vi.mock("../bpp/web/static/js/modules/people-view.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  showPeopleView: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/analysis.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  scheduleRecompute: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people.mjs", async (importOriginal) => ({
  .../** @type {any} */ (await importOriginal()),
  personDisplayName: () => "Leo",
}));

import { excludePerson, includePerson } from "../bpp/web/static/js/modules/people-merge.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

const FPS = ["/a.jpg", "/b.jpg"];

beforeEach(() => {
  /** @type {any} */ (state).faceClusters = [{ cluster_id: 6, filepaths: FPS }];
  /** @type {any} */ (state).overrides = {};
});

function stubFetch(status) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(status === 200 ? {} : { error: "nope" }), {
          status,
          headers: { "content-type": "application/json" },
        })
    )
  );
}

describe("excludePerson — optimistic override revert on failure (review CR)", () => {
  test("success: all photos marked exclude", async () => {
    stubFetch(200);
    await excludePerson(6);
    expect(state.overrides["/a.jpg"]).toBe("exclude");
    expect(state.overrides["/b.jpg"]).toBe("exclude");
  });

  test("failure: overrides roll back to their prior state (not left 'exclude')", async () => {
    stubFetch(500);
    await excludePerson(6);
    // Prior was empty → keys removed, not left at the optimistic "exclude".
    expect("/a.jpg" in state.overrides).toBe(false);
    expect("/b.jpg" in state.overrides).toBe(false);
  });
});

describe("includePerson — optimistic override revert on failure (review CR)", () => {
  beforeEach(() => {
    /** @type {any} */ (state).overrides = { "/a.jpg": "exclude", "/b.jpg": "exclude" };
  });

  test("success: excluded photos cleared", async () => {
    stubFetch(200);
    await includePerson(6);
    expect("/a.jpg" in state.overrides).toBe(false);
    expect("/b.jpg" in state.overrides).toBe(false);
  });

  test("failure: the 'exclude' overrides are restored (not left cleared)", async () => {
    stubFetch(500);
    await includePerson(6);
    expect(state.overrides["/a.jpg"]).toBe("exclude");
    expect(state.overrides["/b.jpg"]).toBe("exclude");
  });
});
