// Verifies the media-route trust-recovery handler installed by
// api-client.mjs. When an <img> or <video> referencing /thumb /photo
// /video fails to load, the handler probes /api/status — and if that
// 403s (revoke / token rotation), the existing apiFetch reload kicks in.
//
// Without this listener, a phone that gets revoked while only scrolling
// thumbs (no API calls in flight) would sit on a broken-image-icon
// page indefinitely.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

describe("media error → trust probe", () => {
  /** @type {ReturnType<typeof vi.fn>} */
  let fetchMock;
  /** @type {ReturnType<typeof vi.fn>} */
  let reload;

  beforeEach(async () => {
    // Fresh module per test so the once-per-session "_sessionLostHandled"
    // flag and the throttle timer don't leak across cases.
    vi.resetModules();
    reload = vi.fn();
    vi.stubGlobal("location", { reload });
    fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    // Importing the module installs the document-level error listener.
    await import("../bpp/web/static/js/modules/api-client.mjs");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /**
   * Simulate an <img> load failure for a given URL.
   * @param {string} url
   * @param {string} [tag]
   */
  function dispatchImgError(url, tag = "IMG") {
    const el = /** @type {any} */ (document.createElement(tag.toLowerCase()));
    el.src = url;
    document.body.appendChild(el);
    // The capture-phase listener doesn't see events dispatched on
    // detached nodes; appending first ensures the path goes through
    // document. The error event itself is non-bubbling, but our
    // listener uses useCapture=true.
    const ev = new Event("error", { bubbles: false });
    el.dispatchEvent(ev);
    el.remove();
  }

  test("probes /api/status when an <img>/thumb fails", async () => {
    dispatchImgError("http://localhost/thumb/abc?_token=x");
    await new Promise((r) => setTimeout(r, 0));
    const probedStatus = fetchMock.mock.calls.some((c) =>
      String(c[0]).startsWith("/api/v1/status")
    );
    expect(probedStatus).toBe(true);
  });

  test("probes for /photo and /video too", async () => {
    dispatchImgError("http://localhost/photo/abc?_token=x");
    dispatchImgError("http://localhost/video/abc?_token=x", "VIDEO");
    await new Promise((r) => setTimeout(r, 0));
    // First probe goes through; second is throttled within 5s — so we
    // expect *at least one* /api/status call. The throttle is the
    // intended behavior (a grid full of failures shouldn't probe-storm).
    const probes = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/v1/status"));
    expect(probes.length).toBeGreaterThanOrEqual(1);
  });

  test("does NOT probe when the failed asset isn't a media URL", async () => {
    dispatchImgError("http://localhost/static/icon.png");
    dispatchImgError("https://placeholder.example.com/foo.jpg");
    await new Promise((r) => setTimeout(r, 0));
    const probes = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/v1/status"));
    expect(probes.length).toBe(0);
  });

  test("triggers reload when the trust probe itself returns 403", async () => {
    // Re-stub fetch to return 403 — the existing apiFetch handler
    // should fire the reload from inside the probe.
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ error: "Forbidden" }), {
          status: 403,
          headers: { "content-type": "application/json" },
        })
    );
    dispatchImgError("http://localhost/thumb/abc?_token=stale");
    // Probe + apiFetch's setTimeout(50) for reload — wait long enough.
    await new Promise((r) => setTimeout(r, 100));
    expect(reload).toHaveBeenCalled();
  });

  test("throttles repeated failures so the grid doesn't probe-storm", async () => {
    // Fire 10 errors back-to-back, all on /thumb URLs.
    for (let i = 0; i < 10; i++) {
      dispatchImgError(`http://localhost/thumb/h${i}?_token=x`);
    }
    await new Promise((r) => setTimeout(r, 0));
    const probes = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/v1/status"));
    // 10 errors → exactly 1 probe (throttle window is 5s).
    expect(probes.length).toBe(1);
  });
});
