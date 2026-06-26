// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn(async () => true),
  appPrompt: vi.fn(),
}));

import { appConfirm } from "../bpp/web/static/js/modules/dialogs.mjs";
import {
  _getPetCtxClusterId,
  _resetPetsState,
  dismissPetCluster,
  getPetAlbumId,
  getPetName,
  hidePetCtxMenu,
  initPetCtxMenu,
  loadPetClusters,
  navigateToPetAlbum,
  navigateToPets,
  petClassFromCluster,
  petDisplayName,
  petLabelHTML,
  renamePet,
  showPetCtxMenu,
  showPetMergePicker,
  showPetsView,
  startPetRename,
} from "../bpp/web/static/js/modules/pets.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <div class="content"></div>
    <div id="status-summary"></div>
    <div id="status-bar" class="hidden"></div>
    <div id="toolbar" class="hidden"></div>
    <div id="toolbar-title"></div>
    <div id="toolbar-subtitle"></div>
    <div id="pet-ctx-menu" class="hidden">
      <div class="ctx-menu-item" data-action="rename"></div>
      <div class="ctx-menu-item" data-action="identify"></div>
      <div class="ctx-menu-item" data-action="merge"></div>
    </div>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).petClusters = [];
  /** @type {any} */ (window).petsAvailable = true;
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "pets";
  /** @type {any} */ (window).ICONS = { paw: "<i>paw</i>" };
  /** @type {any} */ (window).loadAlbumList = vi.fn(async () => {});
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).navigateTo = vi.fn();
  _resetPetsState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "petClusters",
    "petsAvailable",
    "currentAlbumId",
    "currentView",
    "ICONS",
    "loadAlbumList",
    "renderAlbumNav",
    "navigateTo",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetPetsState();
});

/**
 * @param {object} body
 */
function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("loadPetClusters", () => {
  test("populates window.petClusters on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          clusters: [{ cluster_id: 1, photo_count: 5, pet_class: "dog", representative: null }],
        })
      )
    );
    await loadPetClusters();
    expect(/** @type {any} */ (window).petClusters).toHaveLength(1);
  });

  test("on error, preserves prior clusters and surfaces a sidebar sentinel", async () => {
    // P-06: loadPetClusters now routes through wrapSectionLoader so
    // a failure surfaces a retry pill in the Pets section instead of
    // silently wiping the list. Keeping the previous clusters lets
    // the user finish what they were doing before the blip.
    const { getSectionError } = await import("../bpp/web/static/js/modules/sidebar-safety.mjs");
    /** @type {any} */ (window).petClusters = [{ cluster_id: 99 }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await loadPetClusters();
    expect(/** @type {any} */ (window).petClusters).toEqual([{ cluster_id: 99 }]);
    expect(getSectionError("pets")).toBeTruthy();
  });
});

describe("petClassFromCluster / getPetName / getPetAlbumId / petDisplayName", () => {
  test("petClassFromCluster reads from petClusters", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "cat" }];
    expect(petClassFromCluster(5)).toBe("cat");
    expect(petClassFromCluster(99)).toBeNull();
  });

  test("getPetName matches by cluster_id first, then pet_class", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 100, album_type: "smart_pet", name: "Rex", rule: { cluster_id: 5 } },
    ];
    expect(getPetName(5)).toBe("Rex");
    expect(getPetAlbumId(5)).toBe(100);
  });

  test("petDisplayName returns null when name equals default label", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "smart_pet", name: "Dogs", rule: { cluster_id: 5 } },
    ];
    expect(petDisplayName(5)).toBeNull();
  });

  test("petDisplayName returns custom name", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "smart_pet", name: "Buddy", rule: { cluster_id: 5 } },
    ];
    expect(petDisplayName(5)).toBe("Buddy");
  });
});

describe("renamePet", () => {
  test("PUTs to /api/albums/<id> and refreshes", async () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 100, album_type: "smart_pet", name: "Dogs", rule: { cluster_id: 5 } },
    ];
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await renamePet(5, "Buddy");
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const call = calls.find((c) => String(c[0]).includes("/api/v1/albums/100"));
    expect(call).toBeDefined();
    expect(call && call[1].method).toBe("PUT");
  });

  test("noop on empty name", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await renamePet(5, "  ");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("startPetRename", () => {
  test("replaces label content with an input and focuses", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    const label = document.createElement("div");
    label.id = "pet-label-5";
    document.body.appendChild(label);
    startPetRename(5, label);
    expect(label.querySelector("input")).toBeTruthy();
  });
});

describe("petLabelHTML", () => {
  test("default label when unnamed", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    expect(petLabelHTML(5, 7)).toContain("Dogs");
    expect(petLabelHTML(5, 7)).toContain("7 photos");
  });

  test("custom name when named", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "smart_pet", name: "Buddy", rule: { cluster_id: 5 } },
    ];
    expect(petLabelHTML(5, 1)).toContain("Buddy");
    expect(petLabelHTML(5, 1)).toContain("1 photo");
  });
});

describe("showPetsView", () => {
  test("renders empty state when no pets and pets unavailable", () => {
    /** @type {any} */ (window).petsAvailable = false;
    showPetsView();
    expect(document.getElementById("pets-view")?.textContent).toContain("Pet detection");
    expect(document.getElementById("pets-view")?.textContent).toContain("not installed");
  });

  test("renders empty state when no pets but available", () => {
    showPetsView();
    expect(document.getElementById("pets-view")?.textContent).toContain("No Pets Found");
  });

  test("renders one card per cluster sorted by count desc", () => {
    /** @type {any} */ (window).petClusters = [
      {
        cluster_id: 1,
        photo_count: 3,
        pet_class: "cat",
        representative: { thumb_hash: "ha", detection_index: 0 },
      },
      {
        cluster_id: 2,
        photo_count: 7,
        pet_class: "dog",
        representative: { thumb_hash: "hb", detection_index: 0 },
      },
    ];
    showPetsView();
    const cards = document.querySelectorAll(".pet-card");
    expect(cards).toHaveLength(2);
    // first card should be cluster_id=2 (more photos)
    expect(/** @type {HTMLElement} */ (cards[0]).dataset.clusterId).toBe("2");
  });

  test("sets the toolbar subtitle", () => {
    /** @type {any} */ (window).petClusters = [
      { cluster_id: 1, photo_count: 3, pet_class: "cat", representative: null },
    ];
    showPetsView();
    expect(document.getElementById("status-summary")?.textContent).toBe("1 pet group");
  });
});

describe("navigateToPetAlbum / navigateToPets", () => {
  test("navigateToPetAlbum delegates when album exists", async () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    /** @type {any} */ (window).albumList = [
      { id: 100, album_type: "smart_pet", rule: { cluster_id: 5 } },
    ];
    await navigateToPetAlbum(5);
    expect(/** @type {any} */ (window).navigateTo).toHaveBeenCalledWith("album", 100);
  });

  test("navigateToPetAlbum toasts when no album found after refresh", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await navigateToPetAlbum(99);
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Pet album not found"
    );
  });

  test("navigateToPets calls window.navigateTo('pets')", () => {
    navigateToPets();
    expect(/** @type {any} */ (window).navigateTo).toHaveBeenCalledWith("pets");
  });
});

describe("pet context menu", () => {
  test("show / hide toggles hidden class and stores cluster id", () => {
    const evt = new MouseEvent("contextmenu", { clientX: 10, clientY: 10 });
    showPetCtxMenu(evt, 7);
    expect(_getPetCtxClusterId()).toBe(7);
    expect(document.getElementById("pet-ctx-menu")?.classList.contains("hidden")).toBe(false);
    hidePetCtxMenu();
    expect(_getPetCtxClusterId()).toBeNull();
    expect(document.getElementById("pet-ctx-menu")?.classList.contains("hidden")).toBe(true);
  });

  test("clicking 'rename' starts inline rename when label exists", () => {
    initPetCtxMenu();
    const label = document.createElement("div");
    label.id = "pet-label-5";
    document.body.appendChild(label);
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    showPetCtxMenu(new MouseEvent("contextmenu"), 5);
    const renameItem = /** @type {HTMLElement} */ (
      document.querySelector('#pet-ctx-menu .ctx-menu-item[data-action="rename"]')
    );
    renameItem.click();
    expect(label.querySelector("input")).toBeTruthy();
  });
});

describe("dismissPetCluster ('Not a pet')", () => {
  beforeEach(() => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 1, pet_class: "dog", photo_count: 3 }];
    vi.mocked(appConfirm).mockClear();
    vi.mocked(appConfirm).mockResolvedValue(true);
  });

  test("cancel → no request sent", async () => {
    vi.mocked(appConfirm).mockResolvedValueOnce(false);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await dismissPetCluster(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("confirm → POSTs cluster_id, applies albums, reloads clusters", async () => {
    const fetchMock = vi.fn(async (/** @type {string} */ url) => {
      if (String(url).includes("/api/v1/pets/dismiss")) {
        return jsonResp({ status: "ok", count: 3, albums: [{ id: 9, name: "Cats" }] });
      }
      return jsonResp({ clusters: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    await dismissPetCluster(1);

    const dismissCall = /** @type {any[][]} */ (fetchMock.mock.calls).find((c) =>
      String(c[0]).includes("/api/v1/pets/dismiss")
    );
    expect(dismissCall).toBeTruthy();
    expect(JSON.parse(dismissCall?.[1]?.body)).toEqual({ cluster_id: 1 });
    expect(/** @type {any} */ (window).albumList).toEqual([{ id: 9, name: "Cats" }]);
    expect(/** @type {any} */ (window).renderAlbumNav).toHaveBeenCalled();
    // Clusters reloaded from the server (mock returns none → group gone).
    expect(/** @type {any} */ (window).petClusters).toEqual([]);
  });

  test("ctx-menu 'not-a-pet' action routes to the dismiss flow", async () => {
    initPetCtxMenu();
    const menu = /** @type {HTMLElement} */ (document.getElementById("pet-ctx-menu"));
    const item = document.createElement("div");
    item.className = "ctx-menu-item danger";
    item.dataset.action = "not-a-pet";
    menu.appendChild(item);
    const fetchMock = vi.fn(async () => jsonResp({ clusters: [] }));
    vi.stubGlobal("fetch", fetchMock);
    showPetCtxMenu(new MouseEvent("contextmenu"), 1);
    item.click();
    await vi.waitFor(() => expect(appConfirm).toHaveBeenCalled());
  });
});

describe("showPetMergePicker", () => {
  test("renders an empty-state when no other groups", () => {
    /** @type {any} */ (window).petClusters = [{ cluster_id: 5, pet_class: "dog" }];
    showPetMergePicker(5);
    expect(document.querySelector(".merge-picker-empty")?.textContent).toContain(
      "No other pet groups"
    );
  });

  test("renders one item per other cluster", () => {
    /** @type {any} */ (window).petClusters = [
      { cluster_id: 1, photo_count: 5, pet_class: "dog" },
      { cluster_id: 2, photo_count: 3, pet_class: "dog" },
      { cluster_id: 3, photo_count: 2, pet_class: "cat" },
    ];
    showPetMergePicker(1);
    expect(document.querySelectorAll(".merge-picker-item")).toHaveLength(2);
  });
});
