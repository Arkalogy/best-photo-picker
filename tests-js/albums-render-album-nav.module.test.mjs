// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { renderAlbumNav, switchAlbum } from "../bpp/web/static/js/modules/albums.mjs";
import { setup, teardown } from "./_albums-setup.mjs";

beforeEach(setup);
afterEach(teardown);

// ── Sidebar active-state + folder-open persistence ───────────────────
//
// Regression tests for the "selected album highlight is lost on refresh"
// and "manually-expanded folder collapses on every nav" bugs. These pin:
//
//   - renderAlbumNav must apply `.active` to nav items matching
//     currentAlbumId when currentView === "album".
//   - The parent folder of the active album must render `<details open>`
//     (auto-expand around the user's current location).
//   - A user-toggled folder open-state persists via localStorage and
//     survives re-renders that don't touch currentAlbumId.
//   - The Albums-section <details> renders with the data-nav-key so
//     the global toggle listener can persist its state — without this
//     the user's "I want Albums collapsed" preference snaps back on
//     every render because the localStorage value was the only source
//     of truth and the native toggle didn't update it.

describe("renderAlbumNav — active state + open-folder persistence", () => {
  beforeEach(() => {
    // Reset the per-key nav-open map so tests start clean. Other
    // beforeEach resets DOM but not localStorage.
    localStorage.removeItem("bpp-nav-open-keys");
    localStorage.removeItem("bpp-albums-collapsed");
  });

  test("active album receives .active class on the nav-item", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Hawaii", photo_count: 38 },
    ];
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).currentView = "album";
    renderAlbumNav();
    const item = document.querySelector('[data-album-id="7"]');
    expect(item, "Hawaii album item must exist in rendered sidebar").toBeTruthy();
    expect(
      item?.classList.contains("active"),
      "active album must have .active class so the highlight survives a refresh"
    ).toBe(true);
  });

  test("user-clicking a folder summary writes the open state to localStorage", () => {
    // This is the load-bearing one: my unit tests above set localStorage
    // directly and verified renderAlbumNav reads it. But on real refresh
    // the FIRST step is "user clicks summary → toggle event fires → my
    // listener must write to localStorage". If the listener isn't being
    // installed or the toggle event doesn't reach it, the data flow is
    // broken end-to-end even though both halves work in isolation.
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    renderAlbumNav();
    const details = /** @type {HTMLDetailsElement} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(details, "Albums section must render").toBeTruthy();
    expect(details.dataset.navKey).toBe("section:albums");

    // Simulate: user closes the section. Native <details> updates `open`
    // first, then fires `toggle`. We mimic the same order so the
    // listener sees `details.open === false` when the event arrives.
    details.open = false;
    details.dispatchEvent(new Event("toggle"));

    const map = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(
      map["section:albums"],
      "after a user-driven toggle, localStorage must reflect the new state. " +
        "If this fails, the persistence listener isn't catching the native " +
        "<details> toggle event (likely a bubbles/capture issue) — meaning " +
        "every user interaction is lost on refresh."
    ).toBe(false);

    // Reopen.
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    const map2 = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(map2["section:albums"]).toBe(true);
  });

  test("simulated boot flow: switchAlbum after initial render lands active", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Hawaii", photo_count: 38 },
    ];
    // Step 1: app.mjs sets currentAlbumId to all and does initial render.
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).currentView = "library";
    renderAlbumNav();

    // Step 2: saved nav restore — switchAlbum is called for the previously
    // active album. This is the boot path the user sees on Cmd+R.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], has_more: false, selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await switchAlbum(7);

    // After switchAlbum, the sidebar must show Hawaii (id=7) highlighted.
    const item = document.querySelector('[data-album-id="7"]');
    expect(item, "Hawaii item must exist after switchAlbum").toBeTruthy();
    expect(
      item?.classList.contains("active"),
      "boot-time switchAlbum must produce .active on the item — this is the refresh case the user reported"
    ).toBe(true);
    // And Library (id=1) must NOT be marked active anymore.
    const lib = document.querySelector('[data-album-id="1"]');
    expect(
      lib?.classList.contains("active"),
      "Library item must lose .active when we switch away"
    ).toBe(false);
  });

  test("active album inside a Tag folder forces that folder open", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      {
        id: 42,
        album_type: "smart_tag",
        name: "Sunset",
        photo_count: 5,
        rule: { tag_id: 99 },
      },
    ];
    /** @type {any} */ (window).currentAlbumId = 42;
    /** @type {any} */ (window).currentView = "album";
    renderAlbumNav();
    // The Tags <details> wraps the smart_tag entries — must render open
    // because the active album lives inside it.
    const tagDetails = document.querySelector('[data-nav-key="section:tags"]');
    expect(tagDetails, "Tags section must render with data-nav-key").toBeTruthy();
    expect(
      /** @type {HTMLDetailsElement | null} */ (tagDetails)?.open,
      "parent folder of active album must auto-open"
    ).toBe(true);
  });

  test("user-toggled folder open state survives re-render without active child", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      {
        id: 42,
        album_type: "smart_tag",
        name: "Sunset",
        photo_count: 5,
        rule: { tag_id: 99 },
      },
    ];
    // No active album inside Tags. User expanded it anyway.
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:tags": true }));
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).currentView = "library";
    renderAlbumNav();
    const tagDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector('[data-nav-key="section:tags"]')
    );
    expect(
      tagDetails?.open,
      "user-expanded folder must NOT collapse just because the active album isn't inside it"
    ).toBe(true);
  });

  test("Albums section <details> renders with data-nav-key for unified persistence", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    renderAlbumNav();
    const albumsDetails = document.querySelector(
      'details.nav-albums-section[data-nav-key="section:albums"]'
    );
    expect(
      albumsDetails,
      "Albums section must declare data-nav-key so the unified toggle listener captures clicks"
    ).toBeTruthy();
  });

  test("Albums section honors user-stored open=true via the unified key", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:albums": true }));
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open).toBe(true);
  });

  test("Albums section honors user-stored open=false via the unified key", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:albums": false }));
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open).toBe(false);
  });

  test("per-album filter persists across switchAlbum round-trip", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Birthday Party", photo_count: 25 },
      { id: 11, album_type: "manual", name: "Hawaii Trip", photo_count: 38 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    // Make the select behave like a real filter dropdown with the values
    // photos.mjs's onFilterChange writes.
    filterEl.innerHTML =
      '<option value="all">All</option>' +
      '<option value="selected">Selected only</option>' +
      '<option value="favorites">Favorites</option>';

    /** @type {any} */ (window).renderGrid = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    // Step 1: switch to Birthday Party.
    await switchAlbum(7);
    // Step 2: user picks "Selected only" filter while on Birthday Party.
    filterEl.value = "selected";
    /** @type {any} */ (window).onFilterChange();

    // Step 3: switch to Hawaii Trip. Its scope has no saved filter →
    // must default back to "all" (not inherit Birthday Party's).
    await switchAlbum(11);
    expect(
      filterEl.value,
      "Hawaii Trip must default to 'all' — filter is per-album, " +
        "switching away from Birthday Party must NOT carry its filter across"
    ).toBe("all");

    // Step 4: switch back to Birthday Party. Its saved "selected" filter
    // must come back — that's the load-bearing requirement.
    await switchAlbum(7);
    expect(
      filterEl.value,
      "Birthday Party must restore its previously-saved 'selected' filter"
    ).toBe("selected");
  });

  test("per-album filter survives a simulated refresh (boot → switchAlbum)", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Birthday Party", photo_count: 25 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    filterEl.innerHTML =
      '<option value="all">All</option>' + '<option value="selected">Selected only</option>';

    // Seed localStorage as if a previous session saved the filter.
    localStorage.setItem("bpp-album-filters", JSON.stringify({ "album:7": "selected" }));

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    // Simulate boot: switchAlbum is called via savedNav restore.
    await switchAlbum(7);

    expect(
      filterEl.value,
      "after refresh boot, switchAlbum must restore the previously-saved filter from localStorage"
    ).toBe("selected");
  });

  test("filter==='all' is stored as ABSENCE so the map doesn't bloat", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    filterEl.innerHTML =
      '<option value="all">All</option>' + '<option value="selected">Selected only</option>';

    /** @type {any} */ (window).renderGrid = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    await switchAlbum(7);
    // Apply a non-default filter, then revert to "all" — the entry should
    // be deleted from the map rather than stored as "all" verbatim. This
    // keeps the map size proportional to "things the user customized."
    filterEl.value = "selected";
    /** @type {any} */ (window).onFilterChange();
    let map = JSON.parse(localStorage.getItem("bpp-album-filters") || "{}");
    expect(map["album:7"]).toBe("selected");

    filterEl.value = "all";
    /** @type {any} */ (window).onFilterChange();
    map = JSON.parse(localStorage.getItem("bpp-album-filters") || "{}");
    expect(
      Object.prototype.hasOwnProperty.call(map, "album:7"),
      "reverting to default 'all' must clear the entry, not store 'all' explicitly"
    ).toBe(false);
  });

  test("legacy LS_COLLAPSED still honored when unified key is absent (migration path)", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    // Legacy users have only the old key. Should be respected until the
    // first toggle migrates them to the unified scheme.
    localStorage.setItem("bpp-albums-collapsed", "1");
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open, "legacy collapse preference must survive migration").toBe(false);
  });
});
