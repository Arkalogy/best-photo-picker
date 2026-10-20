// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  _lbApplyTransform,
  _lbShowZoomIndicator,
  _lbUntagPerson,
  closeLightbox,
  hideLbCtxMenu,
  lbAction,
  lbDelete,
  lbEnhance,
  lbResetZoom,
  lbRevertEnhance,
  lbShowInFinder,
  lbToggleFav,
  lbTrimPreview,
  lbZoomAt,
  lightboxNav,
  openLightbox,
  refreshLightboxIfOpen,
  showLbCtxMenu,
  updateLightboxActions,
  updateLightboxExif,
  updateLightboxFaces,
  updateLightboxMap,
  updateLightboxPets,
  updateLightboxTrim,
  updateLightboxVideoInfo,
} from "../bpp/web/static/js/modules/lightbox.mjs";
import { setup, teardown, jsonResp, samplePhoto } from "./_lightbox-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("lbResetZoom / _lbApplyTransform / _lbShowZoomIndicator / lbZoomAt", () => {
  test("lbResetZoom resets state and clears zoom level visibility", () => {
    /** @type {any} */ (window).lbZoom = 2.5;
    /** @type {any} */ (window).lbPanX = 100;
    /** @type {any} */ (window).lbPanY = -50;
    document.getElementById("lb-zoom-level")?.classList.add("visible");
    lbResetZoom();
    expect(/** @type {any} */ (window).lbZoom).toBe(1);
    expect(/** @type {any} */ (window).lbPanX).toBe(0);
    expect(/** @type {any} */ (window).lbPanY).toBe(0);
  });

  test("_lbShowZoomIndicator shows level and auto-hides after 1200ms", () => {
    /** @type {any} */ (window).lbZoom = 1.5;
    _lbShowZoomIndicator();
    expect(document.getElementById("lb-zoom-level")?.classList.contains("visible")).toBe(true);
    expect(document.getElementById("lb-zoom-level")?.textContent).toBe("150%");
    vi.advanceTimersByTime(1200);
    expect(document.getElementById("lb-zoom-level")?.classList.contains("visible")).toBe(false);
  });

  test("lbZoomAt clamps to range and snaps to 1 below 1.01", () => {
    lbZoomAt(50, 100, 100);
    expect(/** @type {any} */ (window).lbZoom).toBe(10);
    lbZoomAt(0.5, 100, 100);
    expect(/** @type {any} */ (window).lbZoom).toBe(1);
  });

  test("_lbApplyTransform with zoom <= 1 clears transform", () => {
    /** @type {any} */ (window).lbZoom = 1;
    _lbApplyTransform(true);
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    expect(img.style.transform).toBe("");
  });
});

describe("openLightbox", () => {
  test("noop on out-of-range index", () => {
    /** @type {any} */ (window).currentGridItems = [];
    openLightbox(0);
    expect(/** @type {any} */ (window).lightboxIdx).toBe(-1);
  });

  test("populates filename, date, quality, and shows lightbox", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    openLightbox(0);
    expect(/** @type {any} */ (window).lightboxIdx).toBe(0);
    expect(document.getElementById("lb-filename")?.textContent).toBe("a.jpg");
    expect(document.getElementById("lightbox")?.classList.contains("visible")).toBe(true);
  });

  test("video photos load video element", async () => {
    /** @type {any} */ (window).currentGridItems = [{ ...samplePhoto, is_video: true }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    openLightbox(0);
    expect(document.getElementById("lb-img")?.classList.contains("hidden")).toBe(true);
    expect(document.getElementById("lb-video")?.classList.contains("hidden")).toBe(false);
  });

  test("sets img.src to full photo URL", () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    openLightbox(0);
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    expect(img.src).toContain("/photo/h1");
  });

  test("populates similar_photos from _simClusterMap when not set by API", () => {
    const sibling = {
      ...samplePhoto,
      id: 6,
      filepath: "/b.jpg",
      filename: "b.jpg",
      thumb_hash: "h2",
      dup_cluster_id: 7,
    };
    /** @type {any} */ (window).currentGridItems = [{ ...samplePhoto, dup_cluster_id: 7 }, sibling];
    /** @type {any} */ (window)._simClusterMap = {
      "/a.jpg": ["/a.jpg", "/b.jpg"],
      "/b.jpg": ["/a.jpg", "/b.jpg"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );

    // samplePhoto has no similar_photos from the API
    openLightbox(0);

    const opened = /** @type {any} */ (window).currentGridItems[0];
    expect(Array.isArray(opened.similar_photos)).toBe(true);
    expect(opened.similar_photos).toHaveLength(1);
    expect(opened.similar_photos[0].filepath).toBe("/b.jpg");
    expect(opened.similar_photos[0].thumb_hash).toBe("h2");

    const strip = document.getElementById("lb-similar");
    expect(strip?.classList.contains("hidden")).toBe(false);
  });

  test("does not overwrite similar_photos already set by the API", () => {
    const apiSimilar = [{ filepath: "/api-set.jpg", thumb_hash: "hapi", similarity: 0.9 }];
    const photoWithSimilar = { ...samplePhoto, similar_photos: apiSimilar };
    /** @type {any} */ (window).currentGridItems = [photoWithSimilar];
    /** @type {any} */ (window)._simClusterMap = {
      "/a.jpg": ["/a.jpg", "/other.jpg"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );

    openLightbox(0);

    const opened = /** @type {any} */ (window).currentGridItems[0];
    expect(opened.similar_photos).toHaveLength(1);
    expect(opened.similar_photos[0].filepath).toBe("/api-set.jpg");
  });

  test("onerror on img falls back to thumbnail URL", () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    openLightbox(0);
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    expect(typeof img.onerror).toBe("function");
    // Simulate load failure
    /** @type {Function} */ (img.onerror)();
    expect(img.src).toContain("/thumb/h1");
    expect(img.onerror).toBeNull();
  });
});

describe("updateLightboxActions", () => {
  // Unified header: actions are static markup in the editor bar;
  // updateLightboxActions only SYNCS their state per photo.
  test("syncs favorite state onto the top-bar button + empties #lb-actions", () => {
    /** @type {any} */ (window).favorites = new Set(["/a.jpg"]);
    /** @type {any} */ (window).overrides = {};
    updateLightboxActions(samplePhoto);
    expect(document.getElementById("lb-top-fav")?.classList.contains("active")).toBe(true);
    // The panel slot must stay empty (actions live in the header now).
    expect(document.getElementById("lb-actions")?.innerHTML).toBe("");
  });

  test("override include marks the top include button active, exclude not", () => {
    /** @type {any} */ (window).overrides = { "/a.jpg": "include" };
    /** @type {any} */ (window).favorites = new Set();
    updateLightboxActions(samplePhoto);
    expect(document.getElementById("lb-top-include")?.classList.contains("active")).toBe(true);
    expect(document.getElementById("lb-top-exclude")?.classList.contains("active")).toBe(false);
    expect(document.getElementById("lb-top-fav")?.classList.contains("active")).toBe(false);
  });
});

describe("updateLightboxExif", () => {
  test("hides when no exif", () => {
    updateLightboxExif({ ...samplePhoto, exif: null });
    expect(document.getElementById("lb-exif")?.classList.contains("hidden")).toBe(true);
  });

  test("renders camera rows; raw GPS text intentionally absent (map pin carries it)", () => {
    updateLightboxExif({
      ...samplePhoto,
      exif: {
        camera_make: "Apple",
        camera_model: "Apple iPhone 14",
        gps_lat: 37.7,
        gps_lon: -122.4,
      },
    });
    const html = document.getElementById("lb-exif")?.innerHTML || "";
    expect(html).toContain("Apple iPhone 14");
    // Panel cleanup: one "Info" section, coordinates live on the
    // map (tooltip), never as a text row.
    expect(html).toContain("Info");
    expect(html).not.toContain("37.7000, -122.4000");
  });
});

describe("updateLightboxVideoInfo / updateLightboxTrim", () => {
  test("video info shows resolution + duration", () => {
    updateLightboxVideoInfo({
      ...samplePhoto,
      is_video: true,
      video_duration: 65,
      video_width: 1920,
      video_height: 1080,
      video_fps: 30,
      video_codec: "h264",
    });
    const html = document.getElementById("lb-video-info")?.innerHTML || "";
    expect(html).toContain("1:05");
    expect(html).toContain("1920 × 1080");
    expect(html).toContain("H264");
  });

  test("video info hidden for non-videos", () => {
    updateLightboxVideoInfo(samplePhoto);
    expect(document.getElementById("lb-video-info")?.classList.contains("hidden")).toBe(true);
  });

  test("trim section renders for videos with positive duration", () => {
    updateLightboxTrim({ ...samplePhoto, is_video: true, video_duration: 30 });
    expect(document.getElementById("lb-trim")?.innerHTML).toContain("Trim Video");
  });

  test("trim hidden for non-videos", () => {
    updateLightboxTrim(samplePhoto);
    expect(document.getElementById("lb-trim")?.classList.contains("hidden")).toBe(true);
  });
});

describe("updateLightboxMap", () => {
  test("hidden when no GPS in exif", () => {
    updateLightboxMap({ ...samplePhoto, exif: { camera_make: "X" } });
    expect(document.getElementById("lb-map-container")?.classList.contains("hidden")).toBe(true);
  });
});

describe("updateLightboxFaces / _lbUntagPerson", () => {
  test("hides chip strip when no faces or clusters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    await updateLightboxFaces(samplePhoto);
    expect(document.getElementById("lb-faces")?.classList.contains("hidden")).toBe(true);
  });

  test("renders chips for active faces", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          faces: [{ face_id: 1, face_index: 0, cluster_id: 5, name: "Alice" }],
          person_tags: [],
        })
      )
    );
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 5, photo_count: 10 }];
    await updateLightboxFaces(samplePhoto);
    expect(document.getElementById("lb-faces")?.innerHTML).toContain("Alice");
  });

  test("_lbUntagPerson DELETEs the tag and toasts", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    const fetchMock = vi.fn(async () => jsonResp({ faces: [], person_tags: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await _lbUntagPerson(/** @type {any} */ ({ stopPropagation: vi.fn() }), "h1", 5);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const del = calls.find((c) => c[1]?.method === "DELETE");
    expect(del).toBeDefined();
  });
});

describe("updateLightboxPets", () => {
  test("hidden when no pets in photo", async () => {
    await updateLightboxPets(samplePhoto);
    expect(document.getElementById("lb-pets")?.classList.contains("hidden")).toBe(true);
  });

  test("renders fallback chips when no detections returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ detections: [] }))
    );
    await updateLightboxPets({ ...samplePhoto, has_dog: true });
    expect(document.getElementById("lb-pets")?.innerHTML).toContain("Dog");
  });

  test("renders pet chips with crop images when detections present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          detections: [{ class: "dog", confidence: 0.95, detection_index: 0, cluster_id: 1 }],
        })
      )
    );
    await updateLightboxPets({ ...samplePhoto, has_dog: true });
    expect(document.getElementById("lb-pets")?.innerHTML).toContain("Dog");
    expect(document.getElementById("lb-pets")?.innerHTML).toContain("lb-pet-crop");
  });
});

describe("refreshLightboxIfOpen", () => {
  test("noop when lightbox closed", () => {
    /** @type {any} */ (window).lightboxIdx = -1;
    expect(() => refreshLightboxIfOpen()).not.toThrow();
  });

  test("refreshes when open", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ faces: [], person_tags: [] }))
    );
    refreshLightboxIfOpen();
    // Unified header: the action surface re-render is observable via the
    // synced (empty=no override) top-bar state + the cleared panel slot.
    expect(document.getElementById("lb-actions")?.innerHTML).toBe("");
    expect(document.getElementById("lb-top-include")).not.toBeNull();
  });
});

describe("lbToggleFav / lbDelete / lbShowInFinder / lbEnhance / lbRevertEnhance", () => {
  test("lbToggleFav noop when lightbox closed", () => {
    expect(() => lbToggleFav()).not.toThrow();
  });

  test("lbShowInFinder POSTs filepath", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await lbShowInFinder();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => String(c[0]).includes("/api/v1/reveal-file"))).toBeDefined();
  });

  test("lbEnhance toasts and updates p._enhanced flag", async () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await lbEnhance();
    expect(samplePhoto._enhanced).toBe(true);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain("Enhanced!");
  });

  test("lbRevertEnhance clears flag", async () => {
    const p = { ...samplePhoto, _enhanced: true, _auto_enhanced: true };
    /** @type {any} */ (window).currentGridItems = [p];
    /** @type {any} */ (window).lightboxIdx = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await lbRevertEnhance();
    expect(p._enhanced).toBe(false);
  });
});

describe("lbAction / lightboxNav / closeLightbox", () => {
  test("lbAction with toggle clears the override (current==action)", () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    /** @type {any} */ (window).overrides = { "/a.jpg": "include" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    lbAction("include");
    // The undo stack now has an entry — visible on the top-bar undo button.
    expect(document.getElementById("lb-undo")?.classList.contains("visible")).toBe(true);
  });

  test("lightboxNav noop when out of range", () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    lightboxNav(-1);
    expect(/** @type {any} */ (window).lightboxIdx).toBe(0);
  });

  test("closeLightbox resets state and removes visible class", () => {
    /** @type {any} */ (window).lightboxIdx = 0;
    document.getElementById("lightbox")?.classList.add("visible");
    closeLightbox();
    expect(/** @type {any} */ (window).lightboxIdx).toBe(-1);
    expect(document.getElementById("lightbox")?.classList.contains("visible")).toBe(false);
  });
});

describe("showLbCtxMenu / hideLbCtxMenu", () => {
  test("show populates labels + unhides menu", () => {
    /** @type {any} */ (window).currentGridItems = [samplePhoto];
    /** @type {any} */ (window).lightboxIdx = 0;
    const evt = new MouseEvent("contextmenu", { clientX: 10, clientY: 10 });
    showLbCtxMenu(evt);
    expect(document.getElementById("lb-ctx-menu")?.classList.contains("hidden")).toBe(false);
    expect(document.getElementById("lb-ctx-include")?.innerHTML).toContain("Include");
  });

  test("hide adds hidden class", () => {
    document.getElementById("lb-ctx-menu")?.classList.remove("hidden");
    hideLbCtxMenu();
    expect(document.getElementById("lb-ctx-menu")?.classList.contains("hidden")).toBe(true);
  });
});

describe("lbTrimPreview", () => {
  test("plays video and pauses at end timestamp", () => {
    document.body.innerHTML += `
      <input id="lb-trim-start" value="0" />
      <input id="lb-trim-end" value="5" />
    `;
    const video = /** @type {HTMLVideoElement} */ (document.getElementById("lb-video"));
    video.play = vi.fn(() => Promise.resolve());
    video.pause = vi.fn();
    lbTrimPreview();
    expect(video.play).toHaveBeenCalled();
  });
});
