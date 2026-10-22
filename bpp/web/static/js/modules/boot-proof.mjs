// @ts-check
/**
 * Before the page navigates itself back to the server (after a 403, or
 * when the server comes back from a restart), make sure whoever answers
 * on this origin is this app's own server. The desktop shell proves it
 * with a nonce challenge before its own navigations; the page does the
 * same for the navigations it starts, because any local process can
 * take the port while the server is down, and a reload would hand it
 * the boot key kept in sessionStorage.
 *
 * Pages without a boot key (an open-loopback dev server, a paired
 * phone) have nothing to protect and reload as before.
 */

const _enc = new TextEncoder();

/** @returns {string} the boot key this tab holds, or "" */
export function bootSecret() {
  try {
    return sessionStorage.getItem("bpp_boot") || "";
  } catch {
    return "";
  }
}

/** @param {ArrayBuffer | Uint8Array} bytes */
function _hex(bytes) {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * HMAC-SHA256(secret, nonce) as lowercase hex — the same proof the
 * server's /boot-check returns and the desktop shell checks.
 * @param {string} secret
 * @param {string} nonce
 * @returns {Promise<string>}
 */
export async function bootProof(secret, nonce) {
  const key = await crypto.subtle.importKey(
    "raw",
    _enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return _hex(await crypto.subtle.sign("HMAC", key, _enc.encode(nonce)));
}

function _nonce() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return _hex(b);
}

/**
 * Does the listener on this origin hold our boot key? True when this
 * tab holds no key (nothing to protect).
 * @param {typeof fetch} [fetchImpl]
 * @returns {Promise<boolean>}
 */
export async function listenerIsOurs(fetchImpl) {
  const secret = bootSecret();
  if (!secret) return true;
  const f = fetchImpl || fetch;
  const nonce = _nonce();
  try {
    const r = await f(`/boot-check?nonce=${nonce}`, { signal: AbortSignal.timeout(3000) });
    const proof = r.headers.get("X-Boot-Proof") || "";
    if (!proof) return false;
    return proof === (await bootProof(secret, nonce));
  } catch {
    return false;
  }
}

/**
 * Reload the page without ever handing the boot key to a listener that
 * did not prove it holds it: when the proof fails, the key is forgotten
 * first (a restarted server mints a new one anyway, and its locked page
 * asks for the new link). Resolves true when the listener proved itself.
 * @returns {Promise<boolean>}
 */
export async function reloadSafely() {
  const ours = await listenerIsOurs();
  if (!ours) {
    try {
      sessionStorage.removeItem("bpp_boot");
    } catch {
      /* no-op */
    }
  }
  try {
    window.location.reload();
  } catch {
    /* no-op */
  }
  return ours;
}
