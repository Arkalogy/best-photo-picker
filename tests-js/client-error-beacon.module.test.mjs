// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  BEACON_MAX_PER_WINDOW,
  _resetBeaconThrottle,
  beaconClientError,
} from "../bpp/web/static/js/modules/client-error-beacon.mjs";

beforeEach(() => {
  document.head.innerHTML = '<meta name="auth-token" content="tok123">';
  _resetBeaconThrottle();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true }))
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  document.head.innerHTML = "";
});

describe("beaconClientError", () => {
  test("POSTs the error to /api/v1/client-error with the auth token", () => {
    const sent = beaconClientError({
      message: "boom",
      source: "people.mjs",
      lineno: 1,
      colno: 2,
      stack: "st",
    });
    expect(sent).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = /** @type {any} */ (fetch).mock.calls[0];
    expect(url).toBe("/api/v1/client-error");
    expect(opts.method).toBe("POST");
    expect(opts.headers["X-Auth-Token"]).toBe("tok123");
    expect(JSON.parse(opts.body).message).toBe("boom");
  });

  test("dedupes an exact repeat of the last message", () => {
    beaconClientError({ message: "same" });
    const sent2 = beaconClientError({ message: "same" });
    expect(sent2).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test("caps at BEACON_MAX_PER_WINDOW within the window (storm guard)", () => {
    const now = () => 1000; // frozen clock → nothing ages out
    for (let i = 0; i < 8; i++) beaconClientError({ message: "m" + i }, now);
    expect(fetch).toHaveBeenCalledTimes(BEACON_MAX_PER_WINDOW);
  });

  test("allows new beacons after the window elapses", () => {
    let t = 0;
    const now = () => t;
    for (let i = 0; i < BEACON_MAX_PER_WINDOW; i++) beaconClientError({ message: "a" + i }, now);
    expect(fetch).toHaveBeenCalledTimes(BEACON_MAX_PER_WINDOW);
    t += 10001; // past the 10s window
    beaconClientError({ message: "after" }, now);
    expect(fetch).toHaveBeenCalledTimes(BEACON_MAX_PER_WINDOW + 1);
  });

  test("never throws even if fetch blows up (it runs inside the error boundary)", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        throw new Error("network down");
      })
    );
    expect(() => beaconClientError({ message: "x" })).not.toThrow();
    expect(beaconClientError({ message: "y" })).toBe(false);
  });
});
