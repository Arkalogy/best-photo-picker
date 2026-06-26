// @ts-check
// Module-style tests for the auth/API client.
//
// jsdom doesn't ship a real <meta name="auth-token"> in its document,
// so the module loads with an empty token. That's actually fine — the
// helpers' contract is "if token empty, don't append". We exercise both
// branches by manipulating window.fetch and reading the Request shape.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _authToken,
  apiFetch,
  authedSrc,
  authEventSource,
} from "../bpp/web/static/js/modules/api-client.mjs";

describe("authedSrc", () => {
  test("no-ops when the token is empty (jsdom default)", () => {
    expect(_authToken).toBe("");
    expect(authedSrc("/api/v1/x")).toBe("/api/v1/x");
    expect(authedSrc("/api/v1/x?foo=1")).toBe("/api/v1/x?foo=1");
  });
});

describe("apiFetch", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ ok: true, n: 42 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("attaches X-Auth-Token header and returns parsed JSON", async () => {
    const data = await apiFetch("/api/v1/photos");
    expect(data).toEqual({ ok: true, n: 42 });
    const fetchMock = /** @type {ReturnType<typeof vi.fn>} */ (globalThis.fetch);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/photos",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Auth-Token": "" }),
        signal: expect.any(AbortSignal),
      })
    );
  });

  test("merges caller-provided headers without dropping the auth token", async () => {
    await apiFetch("/api/v1/photos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const fetchMock = /** @type {ReturnType<typeof vi.fn>} */ (globalThis.fetch);
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(opts.headers["X-Auth-Token"]).toBe("");
  });

  test("throws an Error with .status + .body on non-2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "Not found" }), {
            status: 404,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await expect(apiFetch("/api/v1/missing")).rejects.toMatchObject({
      message: "Not found",
      status: 404,
      body: { error: "Not found" },
    });
  });

  test("403 triggers a single page reload (session-lost handling)", async () => {
    // Simulates the revoke-on-Mac case: phone's next API call gets 403,
    // and we want it to redirect to the pair page automatically.
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "Forbidden" }), {
            status: 403,
            headers: { "content-type": "application/json" },
          })
      )
    );
    // Multiple in-flight calls all 403 — should still reload only once
    await Promise.allSettled([
      apiFetch("/api/v1/x").catch(() => {}),
      apiFetch("/api/v1/y").catch(() => {}),
      apiFetch("/api/v1/z").catch(() => {}),
    ]);
    // Reload is fired via setTimeout; flush it
    await new Promise((r) => setTimeout(r, 80));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test("respects a caller-supplied AbortSignal", async () => {
    const ac = new AbortController();
    await apiFetch("/api/v1/photos", { signal: ac.signal });
    const fetchMock = /** @type {ReturnType<typeof vi.fn>} */ (globalThis.fetch);
    expect(fetchMock.mock.calls[0][1].signal).toBe(ac.signal);
  });
});

describe("authEventSource", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "EventSource",
      vi.fn(function MockEventSource(url) {
        // @ts-ignore — minimal mock
        this.url = url;
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("constructs an EventSource with ?_token= appended", () => {
    authEventSource("/api/v1/sse");
    const ESMock = /** @type {ReturnType<typeof vi.fn>} */ (
      /** @type {unknown} */ (globalThis.EventSource)
    );
    expect(ESMock).toHaveBeenCalledWith("/api/v1/sse?_token=");
  });

  test("uses & when the URL already has a query string", () => {
    authEventSource("/api/v1/sse?album=1");
    const ESMock = /** @type {ReturnType<typeof vi.fn>} */ (
      /** @type {unknown} */ (globalThis.EventSource)
    );
    expect(ESMock).toHaveBeenCalledWith("/api/v1/sse?album=1&_token=");
  });
});
