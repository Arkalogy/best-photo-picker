// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _iphCamera,
  _iphCopyPath,
  _iphLoadFaces,
  _iphShowTagPicker,
  _iphSize,
  _iphTagPerson,
  _iphUntagPerson,
  iphRenameFace,
} from "../bpp/web/static/js/modules/inspector.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="iph-face-chips"></div>
    <div id="toast-container"></div>
  `;
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).lightboxIdx = -1;
  /** @type {any} */ (window).updateLightboxFaces = vi.fn();
  /** @type {any} */ (window).personDisplayName = (cid) => (cid === 1 ? "Alice" : null);
  /** @type {any} */ (window).getPersonAlbumId = (cid) => (cid === 1 ? 100 : null);
  /** @type {any} */ (window).loadAlbumList = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "faceClusters",
    "currentGridItems",
    "lightboxIdx",
    "updateLightboxFaces",
    "personDisplayName",
    "getPersonAlbumId",
    "loadAlbumList",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
});

const chips = () => /** @type {HTMLElement} */ (document.getElementById("iph-face-chips"));

describe("_iphCamera", () => {
  test("null when no exif", () => {
    expect(_iphCamera(null)).toBeNull();
    expect(_iphCamera(undefined)).toBeNull();
  });

  test("strips redundant make from model", () => {
    expect(_iphCamera({ make: "Apple", model: "Apple iPhone 15" })).toBe("Apple iPhone 15");
  });

  test("just the make when no model", () => {
    expect(_iphCamera({ make: "Canon" })).toBe("Canon");
  });

  test("just the model when no make", () => {
    expect(_iphCamera({ model: "EOS R5" })).toBe("EOS R5");
  });

  test("null when both fields are empty", () => {
    expect(_iphCamera({})).toBeNull();
  });
});

describe("_iphSize", () => {
  test("null when no exif", () => {
    expect(_iphSize(null)).toBeNull();
  });

  test("formats W × H", () => {
    expect(_iphSize({ width: 4032, height: 3024 })).toBe("4032 × 3024");
  });

  test("null when missing one dimension", () => {
    expect(_iphSize({ width: 100 })).toBeNull();
  });
});

describe("_iphLoadFaces", () => {
  test("'No faces detected' when no faces and no clusters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ faces: [], person_tags: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await _iphLoadFaces("h1");
    expect(chips().textContent).toContain("No faces detected");
  });

  test("renders detected face chips with cluster_id → clickable rename", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              faces: [{ face_index: 0, name: "Alice", cluster_id: 1, bbox_w: 100 }],
              person_tags: [],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await _iphLoadFaces("h1");
    expect(chips().textContent).toContain("Alice");
    expect(chips().querySelector(".iph-face-rename")).toBeTruthy();
  });

  test("flags small faces with iph-face-small class", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              faces: [{ face_index: 0, cluster_id: 1, bbox_w: 30 }],
              person_tags: [],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await _iphLoadFaces("h1");
    expect(chips().querySelector(".iph-face-small")).toBeTruthy();
  });

  test("renders manual person tags with untag X", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              faces: [],
              person_tags: [{ cluster_id: 1, name: "Alice" }],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await _iphLoadFaces("h1");
    expect(chips().querySelector(".iph-face-tagged")).toBeTruthy();
    expect(chips().querySelector(".iph-face-untag")).toBeTruthy();
  });

  test("renders + chip when faceClusters is non-empty", async () => {
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 1, photo_count: 5 }];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ faces: [], person_tags: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await _iphLoadFaces("h1");
    expect(chips().querySelector(".iph-face-add")).toBeTruthy();
  });

  test("on fetch error, renders 'Failed to load faces'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await _iphLoadFaces("h1");
    expect(chips().textContent).toContain("Failed to load faces");
  });
});

describe("_iphUntagPerson", () => {
  test("DELETEs /api/faces/tag and toasts", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    await _iphUntagPerson(/** @type {any} */ ({ stopPropagation: vi.fn() }), "h1", 1);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const deleteCall = calls.find((c) => c[1]?.method === "DELETE");
    expect(deleteCall).toBeDefined();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Person tag removed"
    );
  });
});

describe("_iphTagPerson", () => {
  test("POSTs /api/faces/tag and applies silently (panel re-renders the name)", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    await _iphTagPerson("h1", 1);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const postCall = calls.find((c) => c[1]?.method === "POST");
    expect(postCall).toBeDefined();
    // No toast — _iphLoadFaces re-renders the faces panel with the assigned name.
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });
});

describe("iphRenameFace", () => {
  test("PUTs new name when user enters one", async () => {
    document.body.innerHTML += `
      <div id="confirm-overlay">
        <div class="confirm-dialog"></div>
      </div>
    `;
    const fetchMock = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    /** @type {any} */ (window).resolveConfirm = (
      await import("../bpp/web/static/js/modules/dialogs.mjs")
    ).resolveConfirm;

    const renamePromise = iphRenameFace(/** @type {any} */ ({ stopPropagation: vi.fn() }), 1);
    // Simulate user typing the new name then resolving
    const input = /** @type {HTMLInputElement} */ (document.getElementById("confirm-input"));
    input.value = "Alice K";
    /** @type {any} */ (window).resolveConfirm(true);
    await renamePromise;

    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const putCall = calls.find((c) => c[1]?.method === "PUT");
    expect(putCall).toBeDefined();
    expect(putCall && putCall[0]).toContain("/api/v1/albums/100");
  });
});

describe("_iphShowTagPicker", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  test("creates a picker with one .iph-tag-item per cluster", () => {
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 1, photo_count: 10, representative: { thumb_hash: "h1", face_index: 0 } },
      { cluster_id: 2, photo_count: 5, representative: { thumb_hash: "h2", face_index: 0 } },
    ];
    _iphShowTagPicker(
      /** @type {any} */ ({
        stopPropagation: vi.fn(),
        clientX: 100,
        clientY: 100,
      }),
      "h1"
    );
    const items = document.querySelectorAll(".iph-tag-item");
    expect(items).toHaveLength(2);
    // First item is named (Alice = cluster 1 in beforeEach personDisplayName mock)
    expect(items[0].textContent).toContain("Alice");
  });

  test("'No matches' when search filter excludes everything", () => {
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 1, photo_count: 1, representative: null },
    ];
    _iphShowTagPicker(/** @type {any} */ ({ stopPropagation: vi.fn() }), "h1");
    const search = /** @type {HTMLInputElement} */ (document.querySelector(".iph-tag-search"));
    search.value = "zzzz";
    search.dispatchEvent(new Event("input"));
    expect(document.querySelector(".iph-tag-empty")).toBeTruthy();
  });

  test("re-show replaces, doesn't duplicate the picker", () => {
    /** @type {any} */ (window).faceClusters = [
      { cluster_id: 1, photo_count: 1, representative: null },
    ];
    _iphShowTagPicker(/** @type {any} */ ({ stopPropagation: vi.fn() }), "h1");
    _iphShowTagPicker(/** @type {any} */ ({ stopPropagation: vi.fn() }), "h2");
    expect(document.querySelectorAll("#iph-tag-picker")).toHaveLength(1);
  });
});

describe("_iphCopyPath", () => {
  test("flashes 'Copied!' on success", async () => {
    /** @type {any} */ (navigator).clipboard = {
      writeText: vi.fn().mockResolvedValue(),
    };
    const span = document.createElement("span");
    span.textContent = "/some/path.jpg";
    document.body.appendChild(span);

    _iphCopyPath(span, "/some/path.jpg");
    // Wait for the writeText promise + .then() to flush
    await Promise.resolve();
    await Promise.resolve();
    expect(span.textContent).toBe("Copied!");
    expect(span.classList.contains("iph-copied")).toBe(true);
    // The 1200ms restore-timer is scheduled with real timers; we
    // verify the flash state above and trust the setTimeout path.
  });

  test("toasts on writeText failure", async () => {
    /** @type {any} */ (navigator).clipboard = {
      writeText: vi.fn().mockRejectedValue(new Error("blocked")),
    };
    const span = document.createElement("span");
    span.textContent = "/x";
    document.body.appendChild(span);
    _iphCopyPath(span, "/x");
    // Wait for the catch to run
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't copy the path"
    );
  });
});
