// @ts-check
/**
 * Add Face flow — placeholder commit via the Save toolbar.
 *
 * Commit fires on `mouseup`, not `click`. In Tauri's WKWebView, the
 * click event doesn't reliably dispatch on this floating button —
 * mousedown and mouseup both land on the button but click never fires.
 * Diagnosed empirically; using mouseup sidesteps the quirk and is
 * portable (mouseup fires before click in standard browsers too).
 *
 * The test drives the picker → placeholder → Save mouseup sequence
 * and asserts that POST /api/v1/faces/create actually fires with the
 * chosen cluster + placeholder bbox.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="lightbox">
      <div class="lb-img-wrapper" style="width:1000px;height:800px;position:relative">
        <img id="lb-img" />
      </div>
    </div>
  `;
  // Fake size so getBoundingClientRect reports non-zero in jsdom.
  const wrapper = /** @type {HTMLElement} */ (document.querySelector(".lb-img-wrapper"));
  wrapper.getBoundingClientRect = () =>
    /** @type {any} */ ({
      width: 1000,
      height: 800,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 800,
      x: 0,
      y: 0,
    });

  // Stubs the lightbox module reads off window.
  /** @type {any} */ (globalThis.window).faceClusters = [
    { cluster_id: 0, photo_count: 5, representative: null },
    { cluster_id: 1, photo_count: 3, representative: null },
  ];
  /** @type {any} */ (globalThis.window).personDisplayName = (cid) =>
    cid === 0 ? "Alice" : cid === 1 ? "Bob" : null;
  /** @type {any} */ (globalThis.window).currentGridItems = [];
  /** @type {any} */ (globalThis.window).lightboxIdx = -1;
  /** @type {any} */ (globalThis.window).loadAlbumList = vi.fn();
  /** @type {any} */ (globalThis.window).loadFaceClusters = vi.fn();
  /** @type {any} */ (globalThis.window).getPersonAlbumId = () => null;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("Add Face — Save toolbar commit", () => {
  test("clicking Save POSTs /api/v1/faces/create with the chosen cluster + bbox", async () => {
    // Mock fetch BEFORE the dynamic import so apiFetch sees it.
    const fetchMock = vi.fn(async (url, _opts) => {
      // Auth probe (HEAD /api/v1/health or similar) — let any non-create call through.
      if (typeof url === "string" && url.includes("/api/v1/faces/create")) {
        return new Response(
          JSON.stringify({
            face_id: 99,
            cluster_id: 0,
            person_name: "Alice",
            bbox_pct: { x: 40, y: 35, w: 20, h: 30 },
            matched: true,
            method: "yunet",
            quality: 0.8,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      // Default OK for any incidental fetch (album list refresh, etc.)
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    /** @type {any} */ (globalThis).fetch = fetchMock;

    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");

    // Open the Add Face picker. Use a synthetic event with the
    // properties _lbBeginAddFace touches.
    const fakeEvent = /** @type {any} */ ({
      stopPropagation: () => {},
      preventDefault: () => {},
      clientX: 500,
      clientY: 400,
    });
    mod._lbBeginAddFace(fakeEvent, "abc-hash");

    // Picker should appear with an item for "Alice".
    const picker = document.getElementById("lb-add-face-picker");
    expect(picker, "Add Face picker should render").toBeTruthy();
    const items = picker?.querySelectorAll(".merge-picker-item");
    expect(items?.length, "picker should list existing people + New person").toBeGreaterThan(0);

    // Find the Alice entry (first existing-person item; "New person…" is the sticky last one).
    const aliceItem = Array.from(items || []).find((el) => el.textContent?.includes("Alice"));
    expect(aliceItem, "Alice entry should appear in picker").toBeTruthy();

    // Click Alice → picker closes → placeholder bbox renders + toolbar shows.
    /** @type {HTMLElement} */ (aliceItem).click();
    expect(document.getElementById("lb-add-face-picker"), "picker closes after pick").toBeNull();
    const placeholder = document.querySelector(".lb-face-overlay-placeholder");
    expect(placeholder, "placeholder bbox should appear").toBeTruthy();
    const toolbar = document.getElementById("lb-add-face-toolbar");
    expect(toolbar, "Save/Cancel toolbar should appear").toBeTruthy();

    // Find the Save button. It has text "Save (Alice)".
    const buttons = Array.from(toolbar?.querySelectorAll("button") || []);
    const saveBtn = buttons.find((b) => b.textContent?.startsWith("Save"));
    expect(saveBtn, "Save button should exist").toBeTruthy();

    // Mouseup on Save → must POST /api/v1/faces/create.
    // (Commit fires on mouseup, not click — see file header for why.)
    /** @type {HTMLElement} */ (saveBtn).dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true })
    );

    // Let the async fetch + post-commit code resolve.
    await vi.runAllTimersAsync();
    await Promise.resolve();
    await Promise.resolve();

    const createCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/v1/faces/create")
    );
    expect(createCalls.length, "Save must trigger exactly one POST /api/v1/faces/create").toBe(1);

    // And the body should include the chosen cluster + the placeholder bbox.
    const [, opts] = createCalls[0];
    const body = JSON.parse(/** @type {any} */ (opts).body);
    expect(body.cluster_id).toBe(0);
    expect(body.path_hash).toBe("abc-hash");
    expect(body.bbox_pct).toEqual(
      expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) })
    );
  });

  test("New person… path POSTs new_person_name, not cluster_id", async () => {
    // Regression: the previous design minted cluster_id client-side
    // (max + 1) and POSTed that, which the server rejected with
    // "Unknown cluster_id N" because the cluster row didn't exist
    // yet. The fix delegates cluster minting to the server via the
    // `new_person_name` field, eliminating the broken handshake.
    const fetchMock = vi.fn(async (url, _opts) => {
      if (typeof url === "string" && url.includes("/api/v1/faces/create")) {
        return new Response(
          JSON.stringify({
            face_id: 1,
            cluster_id: 2,
            person_name: "Charlie",
            bbox_pct: { x: 40, y: 35, w: 20, h: 30 },
            method: "yunet",
            quality: 0.8,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    /** @type {any} */ (globalThis).fetch = fetchMock;

    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");

    const fakeEvent = /** @type {any} */ ({
      stopPropagation: () => {},
      preventDefault: () => {},
      clientX: 500,
      clientY: 400,
    });
    mod._lbBeginAddFace(fakeEvent, "abc-hash");

    // Click the "New person…" entry — last item in the picker.
    const picker = document.getElementById("lb-add-face-picker");
    const newPersonItem = Array.from(picker?.querySelectorAll(".merge-picker-item") || []).find(
      (el) => el.textContent?.includes("New person")
    );
    expect(newPersonItem, "New person entry should exist").toBeTruthy();
    /** @type {HTMLElement} */ (newPersonItem).click();

    // Type a name and submit. There are two text inputs in the picker
    // (the search box and the inline name input); the name input is
    // the one with placeholder "Enter name…".
    const nameInput = /** @type {HTMLInputElement} */ (
      picker?.querySelector('input[placeholder="Enter name…"]')
    );
    expect(nameInput, "name input should render after picking New person").toBeTruthy();
    nameInput.value = "Charlie";
    const createBtn = Array.from(picker?.querySelectorAll("button") || []).find(
      (b) => b.textContent === "Create"
    );
    /** @type {HTMLElement} */ (createBtn).click();

    // Placeholder + Save toolbar should now be in the DOM.
    const toolbar = document.getElementById("lb-add-face-toolbar");
    const saveBtn = Array.from(toolbar?.querySelectorAll("button") || []).find((b) =>
      b.textContent?.startsWith("Save")
    );
    expect(saveBtn, "Save button should appear").toBeTruthy();
    expect(saveBtn?.textContent).toContain("Charlie");

    /** @type {HTMLElement} */ (saveBtn).dispatchEvent(
      new MouseEvent("mouseup", { bubbles: true, cancelable: true })
    );
    await vi.runAllTimersAsync();
    await Promise.resolve();
    await Promise.resolve();

    const createCalls = fetchMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/api/v1/faces/create")
    );
    expect(createCalls.length, "Save must POST exactly once").toBe(1);
    const [, opts] = createCalls[0];
    const body = JSON.parse(/** @type {any} */ (opts).body);
    expect(body.new_person_name, "must send new_person_name, not cluster_id").toBe("Charlie");
    expect(
      body.cluster_id,
      "must NOT mint a client-side cluster_id — server allocates it"
    ).toBeUndefined();
  });
});
