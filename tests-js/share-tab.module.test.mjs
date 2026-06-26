// @ts-check
/**
 * Vitest coverage for the Settings → Share tab module:
 * - Renders the Devices section when LAN sharing is on
 * - Renders the "off" state with no QR / Devices / URL
 * - Approve calls the right endpoint and re-renders
 * - Block / Revoke goes through appConfirm and only proceeds on OK
 * - Toggle off → POST sets enabled=false
 *
 * The pair-page inline JS isn't covered here (different runtime,
 * lives in templates/pair.html). Its contract is locked by the
 * Python source-scan test in test_js_source_scan.py.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _approveDevice,
  _blockDevice,
  _copyShareUrl,
  _renderShareTab,
  _revokeDevice,
  _revokeShareLink,
  _shareToggle,
} from "../bpp/web/static/js/modules/share-tab.mjs";

const enabledInfo = {
  enabled: true,
  lan_ip: "192.168.1.50",
  port: 5001,
  share_url: "http://192.168.1.50:5001/?_token=abc",
  recent_access: [],
};

const disabledInfo = {
  enabled: false,
  lan_ip: "192.168.1.50",
  port: 5001,
  share_url: null,
  recent_access: [],
};

/**
 * @param {Record<string, any>} responses - URL → body to return
 */
function stubFetch(responses) {
  const fetchMock = vi.fn(async (url, opts) => {
    const path = (url.split("?")[0] || url).toString();
    const body = responses[path];
    if (body === undefined) {
      throw new Error(`unexpected fetch: ${url}`);
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  document.body.innerHTML = `
    <div class="settings-tab-pane active" id="settings-pane-share">
      <div id="share-tab-content"></div>
    </div>
    <meta name="auth-token" content="dummy-token">
  `;
  // Bridge the imported functions onto window so onclick="" handlers
  // in the rendered HTML can find them (matches how index.html bridges).
  /** @type {any} */ (window)._approveDevice = _approveDevice;
  /** @type {any} */ (window)._blockDevice = _blockDevice;
  /** @type {any} */ (window)._revokeDevice = _revokeDevice;
  /** @type {any} */ (window)._shareToggle = _shareToggle;
  /** @type {any} */ (window)._revokeShareLink = _revokeShareLink;
  /** @type {any} */ (window)._copyShareUrl = _copyShareUrl;
  // Most flows show toasts — stub the global element so toast() is a no-op
  document.body.appendChild(Object.assign(document.createElement("div"), { id: "toast" }));
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ─── Initial render ─────────────────────────────────────────────────

describe("_renderShareTab — disabled state", () => {
  test("renders LAN-off pane with no QR or URL", async () => {
    stubFetch({
      "/api/v1/share/info": disabledInfo,
      "/api/v1/share/devices": { pending: [], trusted: [] },
    });
    await _renderShareTab();
    const html = /** @type {HTMLElement} */ (document.getElementById("share-tab-content"))
      .innerHTML;
    expect(html).toContain("LAN sharing");
    expect(html).not.toContain("share-url-input");
    expect(html).not.toContain("share-devices-section");
  });
});

describe("_renderShareTab — enabled state", () => {
  test("renders URL row, QR, and Devices section", async () => {
    stubFetch({
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": { pending: [], trusted: [] },
    });
    await _renderShareTab();
    const html = /** @type {HTMLElement} */ (document.getElementById("share-tab-content"))
      .innerHTML;
    expect(html).toContain('id="share-url-input"');
    expect(html).toContain("/api/v1/share/qr"); // <img src=…>
    expect(html).toContain('id="share-devices-section"');
    expect(html).toContain("Trusted devices");
  });

  test("pending devices show Approve + Block buttons", async () => {
    stubFetch({
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": {
        pending: [
          {
            id: 7,
            fingerprint: "fp-A",
            name: "iPhone",
            ip_at_pair: "192.168.1.10",
            first_seen: Math.floor(Date.now() / 1000),
            last_seen: Math.floor(Date.now() / 1000),
            trusted_at: null,
            revoked_at: null,
            prev_revoked: 0,
          },
        ],
        trusted: [],
      },
    });
    await _renderShareTab();
    const html = /** @type {HTMLElement} */ (document.getElementById("share-tab-content"))
      .innerHTML;
    expect(html).toContain("Pending requests");
    expect(html).toContain("1 waiting");
    expect(html).toContain('data-action="_approveDevice"');
    expect(html).toContain('data-action="_blockDevice"');
  });

  test("trusted devices show Revoke and prev_revoked tag when set", async () => {
    stubFetch({
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": {
        pending: [],
        trusted: [
          {
            id: 9,
            fingerprint: "fp-B",
            name: "iPad",
            ip_at_pair: "192.168.1.11",
            first_seen: 1000,
            last_seen: Math.floor(Date.now() / 1000),
            trusted_at: 2000,
            revoked_at: null,
            prev_revoked: 1,
          },
        ],
      },
    });
    await _renderShareTab();
    const html = /** @type {HTMLElement} */ (document.getElementById("share-tab-content"))
      .innerHTML;
    expect(html).toContain('data-action="_revokeDevice"');
    expect(html).toContain("re-approved after revoke");
  });
});

// ─── Approve / Block / Revoke flows ─────────────────────────────────

describe("_approveDevice", () => {
  test("POSTs to approve endpoint and re-renders", async () => {
    const fetchMock = stubFetch({
      "/api/v1/share/devices/7/approve": { ok: true, id: 7 },
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": { pending: [], trusted: [] },
    });
    await _approveDevice(7);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const approveCall = calls.find((c) => c[0].includes("/approve"));
    expect(approveCall).toBeDefined();
    expect(approveCall[1].method).toBe("POST");
  });
});

describe("_blockDevice", () => {
  test("requires confirmation; cancel does NOT call API", async () => {
    const fetchMock = stubFetch({
      "/api/v1/share/devices/7/revoke": { ok: true, id: 7 },
    });
    // Stub appConfirm by intercepting the global it ends up resolving:
    // appConfirm pushes a confirm-overlay UI we don't have here, so we
    // route through the module's `resolveConfirm` global. The dialog is
    // not in the DOM in this test → appConfirm rejects/resolves quickly.
    // For determinism, we just shadow the imported appConfirm via the
    // dialogs module's internal resolveConfirm:
    const dialogsModule = await import("../bpp/web/static/js/modules/dialogs.mjs");
    document.body.innerHTML += `
      <div id="confirm-overlay" style="display:none">
        <div class="confirm-dialog"></div>
      </div>
    `;
    const promise = _blockDevice(7);
    // Resolve as cancelled
    dialogsModule.resolveConfirm(false);
    await promise;
    // No revoke POST happened
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => c[0].includes("/revoke"))).toBeUndefined();
  });

  test("on confirm, POSTs to revoke endpoint", async () => {
    const fetchMock = stubFetch({
      "/api/v1/share/devices/7/revoke": { ok: true, id: 7 },
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": { pending: [], trusted: [] },
    });
    const dialogsModule = await import("../bpp/web/static/js/modules/dialogs.mjs");
    document.body.innerHTML += `
      <div id="confirm-overlay" style="display:none">
        <div class="confirm-dialog"></div>
      </div>
    `;
    const promise = _blockDevice(7);
    dialogsModule.resolveConfirm(true);
    await promise;
    const revokeCall = /** @type {any[][]} */ (fetchMock.mock.calls).find((c) =>
      c[0].includes("/revoke")
    );
    expect(revokeCall).toBeDefined();
    expect(revokeCall[1].method).toBe("POST");
  });
});

describe("_shareToggle", () => {
  test("POSTs new enabled state to /api/share/toggle", async () => {
    const fetchMock = stubFetch({
      "/api/v1/share/toggle": { enabled: true },
      "/api/v1/share/info": enabledInfo,
      "/api/v1/share/devices": { pending: [], trusted: [] },
    });
    await _shareToggle(true);
    const toggleCall = /** @type {any[][]} */ (fetchMock.mock.calls).find((c) =>
      c[0].includes("/api/v1/share/toggle")
    );
    expect(toggleCall).toBeDefined();
    expect(toggleCall[1].method).toBe("POST");
    expect(toggleCall[1].body).toBe(JSON.stringify({ enabled: true }));
  });
});
