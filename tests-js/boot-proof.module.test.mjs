// @ts-check
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  bootProof,
  listenerIsOurs,
  reloadSafely,
} from "../bpp/web/static/js/modules/boot-proof.mjs";

const SECRET = "0123456789abcdef0123456789abcdef";

async function expectedProof(nonce) {
  return bootProof(SECRET, nonce);
}

function answering(proofFor) {
  // A listener that answers /boot-check with the given proof function.
  return vi.fn(async (url) => {
    const nonce = /nonce=([0-9a-f]+)/.exec(String(url))?.[1] || "";
    const proof = await proofFor(nonce);
    return {
      ok: true,
      status: 204,
      headers: { get: (h) => (h === "X-Boot-Proof" ? proof : null) },
    };
  });
}

describe("boot proof before a self-navigation", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", webcrypto);
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("a page without a boot key has nothing to protect and never calls the server", async () => {
    const f = vi.fn();
    expect(await listenerIsOurs(/** @type {any} */ (f))).toBe(true);
    expect(f).not.toHaveBeenCalled();
  });

  test("our server proves the nonce with the key", async () => {
    sessionStorage.setItem("bpp_boot", SECRET);
    expect(await listenerIsOurs(/** @type {any} */ (answering(expectedProof)))).toBe(true);
  });

  test("a stranger on the port cannot prove it", async () => {
    sessionStorage.setItem("bpp_boot", SECRET);
    const noProof = answering(async () => "");
    expect(await listenerIsOurs(/** @type {any} */ (noProof))).toBe(false);
    const wrongKey = answering((nonce) => bootProof("another-key", nonce));
    expect(await listenerIsOurs(/** @type {any} */ (wrongKey))).toBe(false);
    const dead = vi.fn(async () => {
      throw new TypeError("connection refused");
    });
    expect(await listenerIsOurs(/** @type {any} */ (dead))).toBe(false);
  });

  test("reloadSafely never navigates into a listener that did not earn it", async () => {
    sessionStorage.setItem("bpp_boot", SECRET);
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    vi.stubGlobal(
      "fetch",
      answering(async () => "")
    );
    const told = vi.fn();
    window.addEventListener("bpp:foreign-listener", told);
    expect(await reloadSafely()).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    expect(told).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("bpp_boot")).toBe(SECRET);
    vi.stubGlobal("fetch", answering(expectedProof));
    expect(await reloadSafely()).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("bpp_boot")).toBe(SECRET);
  });

  test("the proof matches the server's HMAC-SHA256 hex", async () => {
    const { createHmac } = await import("node:crypto");
    const nonce = "deadbeef";
    expect(await bootProof(SECRET, nonce)).toBe(
      createHmac("sha256", SECRET).update(nonce).digest("hex")
    );
  });
});
