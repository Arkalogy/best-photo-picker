// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// The Groups merge-nudge lazy-imports people-pair-review; mock it so the
// nudge is controllable and the showGroupsView tests don't hit the real
// /faces/review-pairs/count endpoint.
const pairMock = vi.hoisted(() => ({ count: 0 }));
vi.mock("../bpp/web/static/js/modules/people-pair-review.mjs", () => ({
  refreshAmbiguousPairCount: vi.fn(async () => {}),
  getAmbiguousPairCount: () => pairMock.count,
}));

import {
  _getFaceGroups,
  _renderGroupsMergeNudge,
  _setFaceGroups,
  groupAvatarsHTML,
  groupDefaultName,
  groupDisplayName,
  groupHasNamedMember,
  loadGroups,
  navigateToGroupAlbum,
  navigateToGroups,
  rankGroupsNamedFirst,
  showGroupsView,
} from "../bpp/web/static/js/modules/groups.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="content">
      <div id="toolbar"></div>
      <div id="status-bar">
        <span id="status-summary"></span>
      </div>
    </div>
  `;
  /** @type {any} */ (window).ICONS = { people: "<svg>P</svg>", group: "<svg>G</svg>" };
  /** @type {any} */ (window).faceRecognitionAvailable = true;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).updateToolbarTitle = vi.fn();
  /** @type {any} */ (window).navigateTo = vi.fn();
  /** @type {any} */ (window).refreshSmartAlbums = vi.fn().mockResolvedValue();
  /** @type {any} */ (window).show = vi.fn();
  _setFaceGroups([]);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).ICONS);
  delete (/** @type {any} */ (window).faceRecognitionAvailable);
  delete (/** @type {any} */ (window).albumList);
  delete (/** @type {any} */ (window).updateToolbarTitle);
  delete (/** @type {any} */ (window).navigateTo);
  delete (/** @type {any} */ (window).refreshSmartAlbums);
  delete (/** @type {any} */ (window).show);
});

describe("groupDisplayName", () => {
  test("null when album_name matches the auto-generated default", () => {
    expect(
      groupDisplayName({
        album_name: "Alice & Bob",
        member_info: [{ name: "Alice" }, { name: "Bob" }],
        photo_count: 5,
      })
    ).toBeNull();
  });

  test("returns album_name when user has renamed it", () => {
    expect(
      groupDisplayName({
        album_name: "Family",
        member_info: [{ name: "Alice" }, { name: "Bob" }],
        photo_count: 5,
      })
    ).toBe("Family");
  });

  test("null when album_name is missing", () => {
    expect(
      groupDisplayName({
        member_info: [{ name: "Alice" }],
        photo_count: 1,
      })
    ).toBeNull();
  });

  test("null for a STALE auto name (member renamed since creation)", () => {
    // Album was named "Person 2 & Person 5" at creation; Person 2 has
    // since been renamed Alice. Not user input — fall back to live names.
    expect(
      groupDisplayName({
        album_name: "Person 2 & Person 5",
        member_info: [{ name: "Alice" }, { name: "Person 5" }],
        photo_count: 5,
      })
    ).toBeNull();
    // Mixed stale: one current member name + one auto token.
    expect(
      groupDisplayName({
        album_name: "Alice & Person 9",
        member_info: [{ name: "Alice" }, { name: "Bob" }],
        photo_count: 5,
      })
    ).toBeNull();
  });

  test("custom name containing ' & ' of non-member words survives", () => {
    expect(
      groupDisplayName({
        album_name: "Beach crew & co",
        member_info: [{ name: "Alice" }, { name: "Bob" }],
        photo_count: 5,
      })
    ).toBe("Beach crew & co");
  });
});

describe("groupDefaultName", () => {
  test("joins members with ' & '", () => {
    expect(
      groupDefaultName({
        member_info: [{ name: "Alice" }, { name: "Bob" }, { name: "Carol" }],
        photo_count: 1,
      })
    ).toBe("Alice & Bob & Carol");
  });
});

describe("groupAvatarsHTML", () => {
  test("renders one .group-stack-img per member, capped at 3", () => {
    const html = groupAvatarsHTML({
      member_info: [
        { name: "A", thumb_hash: "h1", face_index: 0 },
        { name: "B", thumb_hash: "h2", face_index: 0 },
        { name: "C", thumb_hash: "h3", face_index: 0 },
        { name: "D", thumb_hash: "h4", face_index: 0 },
        { name: "E", thumb_hash: "h5", face_index: 0 },
      ],
      photo_count: 1,
    });
    // 3 imgs visible
    const div = document.createElement("div");
    div.innerHTML = html;
    expect(div.querySelectorAll("img.group-stack-img")).toHaveLength(3);
  });

  test("appends '+N' overflow chip when more than 3 members", () => {
    const html = groupAvatarsHTML({
      member_info: Array.from({ length: 7 }, (_, i) => ({
        name: `P${i}`,
        thumb_hash: `h${i}`,
        face_index: 0,
      })),
      photo_count: 1,
    });
    expect(html).toContain("+4");
  });

  test("uses placeholder for members without thumb_hash", () => {
    const html = groupAvatarsHTML({
      member_info: [{ name: "X" }],
      photo_count: 1,
    });
    expect(html).toContain("group-stack-placeholder");
    expect(html).not.toContain("img");
  });
});

describe("showGroupsView", () => {
  test("renders empty state with 'No Groups Found' when faceGroups is empty", () => {
    _setFaceGroups([]);
    showGroupsView();
    const view = document.getElementById("groups-view");
    expect(view).toBeTruthy();
    expect(view.textContent).toContain("No Groups Found");
    expect(/** @type {any} */ (window).updateToolbarTitle).toHaveBeenCalledWith(
      "Groups",
      "No groups"
    );
  });

  test("when faceRecognition is unavailable, prompts to install", () => {
    /** @type {any} */ (window).faceRecognitionAvailable = false;
    showGroupsView();
    const view = document.getElementById("groups-view");
    expect(view.textContent).toContain("not installed");
  });

  test("renders one .person-card per group with photo count + name", () => {
    _setFaceGroups([
      {
        member_info: [{ name: "Alice" }, { name: "Bob" }],
        photo_count: 5,
      },
      {
        album_name: "Custom Family",
        member_info: [{ name: "Carol" }, { name: "Dave" }],
        photo_count: 1,
      },
    ]);
    showGroupsView();
    const cards = document.querySelectorAll(".person-card.group-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain("Alice & Bob");
    expect(cards[0].textContent).toContain("5 photos");
    expect(cards[1].textContent).toContain("Custom Family");
    expect(cards[1].textContent).toContain("1 photo");
    expect(cards[1].textContent).not.toContain("1 photos");
  });

  test("subtitle is '1 group' singular / '2 groups' plural", () => {
    _setFaceGroups([{ member_info: [{ name: "A" }, { name: "B" }], photo_count: 1 }]);
    showGroupsView();
    expect(document.getElementById("status-summary").textContent).toBe("1 group");

    _setFaceGroups([
      { member_info: [{ name: "A" }, { name: "B" }], photo_count: 1 },
      { member_info: [{ name: "C" }, { name: "D" }], photo_count: 2 },
    ]);
    showGroupsView();
    expect(document.getElementById("status-summary").textContent).toBe("2 groups");
  });
});

describe("loadGroups", () => {
  test("populates faceGroups from server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              groups: [{ member_info: [{ name: "X" }, { name: "Y" }], photo_count: 1 }],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await loadGroups();
    expect(_getFaceGroups()).toHaveLength(1);
  });

  test("on failure, preserves prior list and surfaces a sidebar sentinel", async () => {
    // P-06: loadGroups now routes through wrapSectionLoader. The
    // previous behavior was to silently reset faceGroups to [] on
    // any failure — that blanked the Groups sidebar section without
    // telling the user why. The new behavior keeps the previous
    // list visible while the wrapper surfaces a retry pill.
    const { getSectionError } = await import("../bpp/web/static/js/modules/sidebar-safety.mjs");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      })
    );
    const prior = /** @type {any} */ ({
      member_info: [{ name: "X" }, { name: "Y" }],
      photo_count: 1,
    });
    _setFaceGroups([prior]);
    await loadGroups();
    // Prior list is preserved (no silent wipe).
    expect(_getFaceGroups()).toEqual([prior]);
    // And the sidebar-safety registry has a "groups" sentinel so
    // the sidebar can render the retry pill.
    expect(getSectionError("groups")).toBeTruthy();
  });
});

describe("navigateToGroups", () => {
  test("calls navigateTo('groups')", () => {
    navigateToGroups();
    expect(/** @type {any} */ (window).navigateTo).toHaveBeenCalledWith("groups");
  });
});

describe("navigateToGroupAlbum", () => {
  test("no-op for invalid index", async () => {
    _setFaceGroups([]);
    await navigateToGroupAlbum(99);
    expect(/** @type {any} */ (window).navigateTo).not.toHaveBeenCalled();
  });

  test("navigates to existing album when album_id is in albumList", async () => {
    _setFaceGroups([
      /** @type {any} */ ({
        album_id: 42,
        member_info: [{ name: "A" }, { name: "B" }],
        photo_count: 1,
      }),
    ]);
    /** @type {any} */ (window).albumList = [{ id: 42, name: "AB" }];
    await navigateToGroupAlbum(0);
    expect(/** @type {any} */ (window).navigateTo).toHaveBeenCalledWith("album", 42);
  });

  test("refreshes smart albums then navigates when album doesn't exist yet", async () => {
    _setFaceGroups([
      /** @type {any} */ ({
        member_info: [{ name: "A" }, { name: "B" }],
        photo_count: 1,
      }),
    ]);
    /** @type {any} */ (window).albumList = [];
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls++;
        // Second call (loadGroups after refreshSmartAlbums) returns the
        // group with an album_id this time.
        if (calls >= 1) {
          return new Response(
            JSON.stringify({
              groups: [
                {
                  album_id: 99,
                  member_info: [{ name: "A" }, { name: "B" }],
                  photo_count: 1,
                },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          );
        }
        return new Response("{}", { status: 200 });
      })
    );
    await navigateToGroupAlbum(0);
    expect(/** @type {any} */ (window).refreshSmartAlbums).toHaveBeenCalled();
    expect(/** @type {any} */ (window).navigateTo).toHaveBeenCalledWith("album", 99);
  });
});

describe("rankGroupsNamedFirst / groupHasNamedMember (v1 visual slice)", () => {
  const mk = (...names) => ({ member_info: names.map((n) => ({ name: n })) });

  test("groupHasNamedMember: true only with a non-'Person N' member", () => {
    expect(groupHasNamedMember(mk("Person 2", "Person 5"))).toBe(false);
    expect(groupHasNamedMember(mk("Person 2", "Anna"))).toBe(true);
    expect(groupHasNamedMember({ member_info: [] })).toBe(false);
    expect(groupHasNamedMember({})).toBe(false);
  });

  test("ranks named-member groups first, preserving original order within each tier", () => {
    const groups = [
      mk("Person 1", "Person 2"), // auto
      mk("Anna", "Person 3"), // named
      mk("Person 4", "Person 5"), // auto
      mk("Bob", "Carol"), // named
    ];
    const ranked = rankGroupsNamedFirst(groups);
    expect(ranked.map((g) => g.member_info[0].name)).toEqual([
      "Anna", // named, in original relative order
      "Bob",
      "Person 1", // auto, original relative order
      "Person 4",
    ]);
  });

  test("pure — does not mutate the input array", () => {
    const groups = [mk("Person 1"), mk("Anna")];
    rankGroupsNamedFirst(groups);
    expect(groups[0].member_info[0].name).toBe("Person 1"); // unchanged
  });

  test("empty / missing input is safe", () => {
    expect(rankGroupsNamedFirst([])).toEqual([]);
    expect(rankGroupsNamedFirst(null)).toEqual([]);
  });
});

describe("_renderGroupsMergeNudge (merge prompt from Groups)", () => {
  afterEach(() => {
    pairMock.count = 0;
  });

  test("shows the banner + Review-merges button when same-person pairs exist", async () => {
    pairMock.count = 5;
    const view = document.createElement("div");
    await _renderGroupsMergeNudge(view);
    const banner = view.querySelector(".groups-merge-nudge");
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("5 pairs");
    expect(banner.querySelector('[data-action="startFacePairReview"]')).toBeTruthy();
    // banner is prepended (above the group cards)
    expect(view.firstChild).toBe(banner);
  });

  test("singular copy for a single pair", async () => {
    pairMock.count = 1;
    const view = document.createElement("div");
    await _renderGroupsMergeNudge(view);
    expect(view.querySelector(".groups-merge-nudge").textContent).toContain("1 pair look");
  });

  test("no banner when there are zero ambiguous pairs", async () => {
    pairMock.count = 0;
    const view = document.createElement("div");
    await _renderGroupsMergeNudge(view);
    expect(view.querySelector(".groups-merge-nudge")).toBeNull();
  });

  test("re-render replaces the old banner (no duplicates)", async () => {
    pairMock.count = 3;
    const view = document.createElement("div");
    await _renderGroupsMergeNudge(view);
    await _renderGroupsMergeNudge(view);
    expect(view.querySelectorAll(".groups-merge-nudge")).toHaveLength(1);
  });
});
