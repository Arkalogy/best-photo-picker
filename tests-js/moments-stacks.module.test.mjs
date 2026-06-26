// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// vi.hoisted: these mock fns must exist before the (hoisted) vi.mock
// factories run, since the static `import` below is hoisted above plain
// const declarations.
const { apiFetch, appConfirm, toast, toastError } = vi.hoisted(() => ({
  apiFetch: vi.fn(async () => ({})),
  appConfirm: vi.fn(async () => true),
  toast: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/compare-sibling.mjs", () => ({
  openCompareWithSibling: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch,
  authedSrc: (/** @type {string} */ s) => s,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({ appConfirm }));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast, toastError }));

import { openCompareWithSibling } from "../bpp/web/static/js/modules/compare-sibling.mjs";
import {
  _crownMomentKeeper,
  _pruneMomentBurst,
  _resetMomentFlyout,
  buildMomentStacks,
  bulkPrunePlan,
  expandMomentStack,
  initMomentBurstFlyout,
  openMomentStack,
} from "../bpp/web/static/js/modules/moments-stacks.mjs";

/** A burst: same moment_cluster_id + moment_size, varying score. */
function photo(fp, mid, size, score, date) {
  return {
    filepath: fp,
    moment_cluster_id: mid,
    moment_size: size,
    aggregate_score: score,
    date,
  };
}

describe("buildMomentStacks", () => {
  test("collapses each burst to one cover, count + non-keeper siblings", () => {
    const photos = [
      photo("/a1.jpg", 1, 2, 0.7, "2024-07-23"),
      photo("/a2.jpg", 1, 2, 0.9, "2024-07-23"), // higher score → keeper
      photo("/b1.jpg", 2, 3, 0.8, "2024-07-20"),
      photo("/b2.jpg", 2, 3, 0.6, "2024-07-20"),
      photo("/b3.jpg", 2, 3, 0.5, "2024-07-20"),
    ];
    const covers = buildMomentStacks(photos);
    expect(covers.length).toBe(2);
    // Ordered by earliest date — burst 2 (Jul 20) before burst 1 (Jul 23).
    expect(covers[0].filepath).toBe("/b1.jpg"); // keeper of burst 2
    expect(covers[0]._momentCount).toBe(3);
    expect(covers[0]._momentSiblings.map((p) => p.filepath)).toEqual(["/b2.jpg", "/b3.jpg"]);
    expect(covers[1].filepath).toBe("/a2.jpg"); // keeper of burst 1
    expect(covers[1]._momentCount).toBe(2);
    expect(covers[1]._momentSiblings.map((p) => p.filepath)).toEqual(["/a1.jpg"]);
  });

  test("ignores singletons / non-Moment photos", () => {
    const photos = [
      photo("/solo.jpg", 0, 1, 0.9, "2024-01-01"),
      photo("/x.jpg", 5, 1, 0.8, "2024-01-02"), // moment_size 1 → not a burst
    ];
    expect(buildMomentStacks(photos)).toEqual([]);
  });

  test("does not mutate the shared photo objects (badge must not leak)", () => {
    const photos = [photo("/a1.jpg", 1, 2, 0.7, "d"), photo("/a2.jpg", 1, 2, 0.9, "d")];
    buildMomentStacks(photos);
    expect(photos[0]._momentCount).toBeUndefined();
    expect(photos[1]._momentSiblings).toBeUndefined();
  });

  test("empty input is safe", () => {
    expect(buildMomentStacks([])).toEqual([]);
    expect(buildMomentStacks(null)).toEqual([]);
  });

  test("excludes trashed photos; a burst that falls below 2 live members vanishes", () => {
    const photos = [
      photo("/a1.jpg", 1, 2, 0.7, "d"),
      { ...photo("/a2.jpg", 1, 2, 0.9, "d"), deleted_at: "2026-06-16" }, // trashed
      photo("/b1.jpg", 2, 3, 0.8, "d"),
      photo("/b2.jpg", 2, 3, 0.6, "d"),
      { ...photo("/b3.jpg", 2, 3, 0.5, "d"), deleted_at: "2026-06-16" }, // trashed
    ];
    const covers = buildMomentStacks(photos);
    // Burst 1 had 2, one trashed → only /a1 live → drops out entirely.
    expect(covers.find((c) => c.filepath === "/a1.jpg")).toBeUndefined();
    // Burst 2 had 3, one trashed → 2 live → stays, count reflects live members.
    const b = covers.find((c) => c._momentSiblings || c.filepath === "/b1.jpg");
    expect(b._momentCount).toBe(2);
    expect(b._momentSiblings.map((p) => p.filepath)).toEqual(["/b2.jpg"]);
  });
});

describe("openMomentStack", () => {
  beforeEach(() => vi.mocked(openCompareWithSibling).mockClear());
  afterEach(() => vi.restoreAllMocks());

  test("opens compare on the burst (cover as parent, siblings, idx 0)", () => {
    const sibs = [{ filepath: "/a1.jpg" }];
    const cover = { filepath: "/a2.jpg", _momentSiblings: sibs, _momentCount: 2 };
    openMomentStack(cover);
    expect(openCompareWithSibling).toHaveBeenCalledWith(cover, sibs, 0);
  });

  test("no-op when siblings haven't loaded", () => {
    openMomentStack({ filepath: "/a.jpg", _momentSiblings: [] });
    openMomentStack({ filepath: "/a.jpg" });
    expect(openCompareWithSibling).not.toHaveBeenCalled();
  });
});

describe("expandMomentStack (burst flyout)", () => {
  beforeEach(() => {
    document.body.innerHTML =
      '<div id="photo-grid"><div class="card moment-stack" data-idx="0"></div></div>';
    /** @type {any} */ (window).currentGridItems = [
      {
        filepath: "/keeper.jpg",
        thumb_hash: "hk",
        aggregate_score: 0.9,
        _momentCount: 3,
        _momentSiblings: [
          { filepath: "/s1.jpg", thumb_hash: "h1", aggregate_score: 0.7 },
          { filepath: "/s2.jpg", thumb_hash: "h2", aggregate_score: 0.6 },
        ],
      },
    ];
    vi.mocked(openCompareWithSibling).mockClear();
  });
  afterEach(() => {
    document.getElementById("moment-burst-flyout")?.remove();
    document.body.innerHTML = "";
    delete (/** @type {any} */ (window).currentGridItems);
  });

  test("expands the burst flyout with keeper + every sibling", () => {
    expandMomentStack(0);
    const fly = document.getElementById("moment-burst-flyout");
    expect(fly).not.toBeNull();
    expect(fly?.classList.contains("hidden")).toBe(false);
    const thumbs = fly?.querySelectorAll(".moment-burst-thumb");
    expect(thumbs?.length).toBe(3); // keeper + 2 siblings
    expect(fly?.querySelectorAll(".is-keeper").length).toBe(1);
  });

  test("clicking a sibling thumb opens compare focused on it", () => {
    expandMomentStack(0);
    const fly = document.getElementById("moment-burst-flyout");
    // data-sib-idx: keeper=-1, then 0,1 for siblings → click the 2nd sibling.
    const thumbs = /** @type {NodeListOf<HTMLElement>} */ (
      fly?.querySelectorAll(".moment-burst-thumb")
    );
    thumbs[2].click();
    const call = vi.mocked(openCompareWithSibling).mock.calls.at(-1);
    expect(call?.[0].filepath).toBe("/keeper.jpg"); // parent = keeper
    expect(call?.[2]).toBe(1); // sibling index 1 (the 2nd sibling)
    // flyout STAYS open (pinned) so it survives the compare round-trip —
    // closing compare lands the user back on the same expanded strip.
    expect(fly?.classList.contains("hidden")).toBe(false);
  });

  test("clicking the keeper thumb compares keeper vs sibling 0", () => {
    expandMomentStack(0);
    const thumbs = /** @type {NodeListOf<HTMLElement>} */ (
      document.querySelectorAll("#moment-burst-flyout .moment-burst-thumb")
    );
    thumbs[0].click(); // keeper (data-sib-idx=-1)
    expect(vi.mocked(openCompareWithSibling).mock.calls.at(-1)?.[2]).toBe(0);
  });
});

describe("initMomentBurstFlyout (hover / grace-close / pin)", () => {
  const cover = (fp, sib) => ({
    filepath: fp,
    thumb_hash: "h" + fp,
    aggregate_score: 0.9,
    _momentCount: 2,
    _momentSiblings: [{ filepath: sib, thumb_hash: "hs", aggregate_score: 0.7 }],
  });

  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML =
      '<div id="photo-grid">' +
      '<div class="card moment-stack" data-idx="0"></div>' +
      '<div class="card moment-stack" data-idx="1"></div>' +
      "</div>" +
      '<div id="compare-overlay"></div><div id="elsewhere"></div>';
    /** @type {any} */ (window).currentGridItems = [
      cover("/k0.jpg", "/s0.jpg"),
      cover("/k1.jpg", "/s1.jpg"),
    ];
    initMomentBurstFlyout(); // idempotent — safe to call every test
  });
  afterEach(() => {
    _resetMomentFlyout();
    vi.useRealTimers();
    document.body.innerHTML = "";
    delete (/** @type {any} */ (window).currentGridItems);
  });

  const hover = (sel) =>
    document.querySelector(sel)?.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  const hidden = () =>
    document.getElementById("moment-burst-flyout")?.classList.contains("hidden") ?? true;

  test("hover opens the strip only after the delay (no instant flash)", () => {
    hover('.card[data-idx="0"]');
    vi.advanceTimersByTime(199);
    expect(hidden()).toBe(true); // not yet
    vi.advanceTimersByTime(1);
    expect(hidden()).toBe(false); // opened at 200ms
  });

  test("scrolling past (leave before the delay) cancels the open", () => {
    hover('.card[data-idx="0"]');
    vi.advanceTimersByTime(100);
    hover("#elsewhere"); // moved off before 200ms
    vi.advanceTimersByTime(300);
    expect(hidden()).toBe(true); // never opened
  });

  test("grace-close after moving off; re-entering the strip cancels the close", () => {
    hover('.card[data-idx="0"]');
    vi.advanceTimersByTime(200);
    expect(hidden()).toBe(false);
    hover("#elsewhere"); // off both → schedule grace close
    vi.advanceTimersByTime(219);
    expect(hidden()).toBe(false); // grace not elapsed yet (reachability window)
    hover("#moment-burst-flyout"); // reached the strip → cancels close
    vi.advanceTimersByTime(500);
    expect(hidden()).toBe(false); // stays open
    hover("#elsewhere");
    vi.advanceTimersByTime(220);
    expect(hidden()).toBe(true); // finally closes
  });

  test("pinned strip stays while compare is visible, closes once compare hides", () => {
    expandMomentStack(0); // click path → pinned + open
    expect(hidden()).toBe(false);
    document.getElementById("compare-overlay")?.classList.add("visible");
    hover("#elsewhere"); // off both, but compare visible → stays
    vi.advanceTimersByTime(500);
    expect(hidden()).toBe(false);
    document.getElementById("compare-overlay")?.classList.remove("visible"); // compare closed
    hover("#elsewhere"); // unpins + schedules close
    vi.advanceTimersByTime(220);
    expect(hidden()).toBe(true);
  });
});

describe("bulkPrunePlan (keep one, trash the rest)", () => {
  const cover = {
    filepath: "/keeper.jpg",
    _momentSiblings: [{ filepath: "/s1.jpg" }, { filepath: "/s2.jpg" }],
  };

  test("default keeper = the computed keeper (cover); trash all siblings", () => {
    const { keep, trash } = bulkPrunePlan(cover);
    expect(keep).toBe("/keeper.jpg");
    expect(trash).toEqual(["/s1.jpg", "/s2.jpg"]);
  });

  test("override keeper = a sibling; trash the cover + other siblings, never the keeper", () => {
    const { keep, trash } = bulkPrunePlan(cover, "/s1.jpg");
    expect(keep).toBe("/s1.jpg");
    expect(trash).toEqual(["/keeper.jpg", "/s2.jpg"]);
    expect(trash).not.toContain("/s1.jpg");
  });

  test("singleton / no siblings → nothing to trash", () => {
    expect(bulkPrunePlan({ filepath: "/solo.jpg", _momentSiblings: [] }).trash).toEqual([]);
    expect(bulkPrunePlan({ filepath: "/solo.jpg" }).trash).toEqual([]);
  });
});

describe("burst prune UI — crown override + Trash the rest", () => {
  beforeEach(() => {
    document.body.innerHTML =
      '<div id="photo-grid"><div class="card moment-stack" data-idx="0"></div></div>';
    const burst = [
      { filepath: "/keeper.jpg", thumb_hash: "hk", aggregate_score: 0.9 },
      { filepath: "/s1.jpg", thumb_hash: "h1", aggregate_score: 0.7 },
      { filepath: "/s2.jpg", thumb_hash: "h2", aggregate_score: 0.6 },
    ];
    /** @type {any} */ (window).currentGridItems = [
      { ...burst[0], _momentCount: 3, _momentSiblings: [burst[1], burst[2]] },
    ];
    /** @type {any} */ (window).photos = burst.map((p) => ({ ...p }));
    /** @type {any} */ (window).renderGrid = vi.fn();
    apiFetch.mockClear();
    appConfirm.mockClear();
    appConfirm.mockResolvedValue(true);
    toast.mockClear();
    toastError.mockClear();
  });
  afterEach(() => {
    _resetMomentFlyout();
    document.getElementById("moment-burst-flyout")?.remove();
    document.body.innerHTML = "";
    delete (/** @type {any} */ (window).currentGridItems);
    delete (/** @type {any} */ (window).photos);
    delete (/** @type {any} */ (window).renderGrid);
  });

  const fly = () => document.getElementById("moment-burst-flyout");
  /** Parsed JSON body of the recorded apiFetch call whose URL matches. */
  const callBody = (/** @type {string} */ url) => {
    const call = /** @type {any} */ (
      apiFetch.mock.calls.find((/** @type {any} */ c) => String(c[0]).includes(url))
    );
    return call ? JSON.parse(call[1].body) : null;
  };

  test("computed keeper is pre-crowned; footer trashes the other 2", () => {
    expandMomentStack(0);
    const crowns = /** @type {NodeListOf<HTMLElement>} */ (fly().querySelectorAll(".mbt-crown"));
    expect(crowns[0].classList.contains("active")).toBe(true); // keeper crowned
    expect(crowns[1].classList.contains("active")).toBe(false);
    expect(fly().querySelector(".mbt-prune")?.textContent).toContain("Trash the other 2");
  });

  test("clicking a sibling's crown re-designates the keeper (no compare opened)", () => {
    expandMomentStack(0);
    vi.mocked(openCompareWithSibling).mockClear();
    const crowns = /** @type {NodeListOf<HTMLElement>} */ (fly().querySelectorAll(".mbt-crown"));
    crowns[1].click(); // crown the first sibling
    const thumbs = /** @type {NodeListOf<HTMLElement>} */ (
      fly().querySelectorAll(".moment-burst-thumb")
    );
    expect(thumbs[1].classList.contains("is-keeper")).toBe(true);
    expect(thumbs[0].classList.contains("is-keeper")).toBe(false);
    expect(openCompareWithSibling).not.toHaveBeenCalled(); // crown ≠ compare
  });

  test("Trash the rest: confirm → trashes non-keepers, marks deleted, 20s Undo", async () => {
    expandMomentStack(0);
    await _pruneMomentBurst();
    expect(appConfirm).toHaveBeenCalled();
    expect(callBody("/photos/delete")?.filepaths).toEqual(["/s1.jpg", "/s2.jpg"]);
    // in-memory deleted + live grid update
    const win = /** @type {any} */ (window);
    expect(win.photos.find((p) => p.filepath === "/s1.jpg").deleted_at).toBeTruthy();
    expect(win.photos.find((p) => p.filepath === "/keeper.jpg").deleted_at).toBeFalsy();
    expect(win.renderGrid).toHaveBeenCalled();
    const t = toast.mock.calls.at(-1);
    expect(t?.[2]?.duration).toBe(20000);
    expect(t?.[2]?.action?.label).toBe("Undo");
  });

  test("crowned override changes what gets trashed", async () => {
    expandMomentStack(0);
    const crowns = /** @type {NodeListOf<HTMLElement>} */ (fly().querySelectorAll(".mbt-crown"));
    crowns[1].click(); // crown /s1.jpg instead
    await _pruneMomentBurst();
    expect(callBody("/photos/delete")?.filepaths).toEqual(["/keeper.jpg", "/s2.jpg"]);
  });

  test("Undo restores the trashed shots", async () => {
    expandMomentStack(0);
    await _pruneMomentBurst();
    const undo = toast.mock.calls.at(-1)?.[2]?.action?.fn;
    await undo();
    expect(callBody("/photos/restore")?.filepaths).toEqual(["/s1.jpg", "/s2.jpg"]);
    const win = /** @type {any} */ (window);
    expect(win.photos.find((p) => p.filepath === "/s1.jpg").deleted_at).toBeFalsy();
  });

  test("cancel at the confirm → nothing trashed", async () => {
    appConfirm.mockResolvedValue(false);
    expandMomentStack(0);
    await _pruneMomentBurst();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
