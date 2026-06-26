// @ts-check
/**
 * Settings → Share tab. Surfaces the LAN sharing toggle, the share URL
 * with QR code, a "Devices" section (TOFU pairing — pending requests
 * to approve, trusted devices to revoke), a revoke-link button, and a
 * recent-access list.
 *
 * Auto-polls every 3s while the tab is open so pending pairing
 * requests appear in real time. Polling stops when the tab is closed.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * @typedef {Object} ShareAccess
 * @property {number} ts
 * @property {string} ip
 * @property {string} user_agent
 *
 * @typedef {Object} ShareInfo
 * @property {boolean} enabled
 * @property {string|null} lan_ip
 * @property {number} port
 * @property {string|null} share_url
 * @property {ShareAccess[]} [recent_access]
 *
 * @typedef {Object} ShareDevice
 * @property {number} id
 * @property {string} fingerprint
 * @property {string} name
 * @property {string} ip_at_pair
 * @property {number} first_seen
 * @property {number} last_seen
 * @property {number|null} trusted_at
 * @property {number|null} revoked_at
 * @property {number} prev_revoked
 *
 * @typedef {Object} DevicesPayload
 * @property {ShareDevice[]} pending
 * @property {ShareDevice[]} trusted
 */

/** @type {ReturnType<typeof setInterval> | null} */
let _shareTabPollTimer = null;

export async function _renderShareTab() {
  const container = document.getElementById("share-tab-content");
  if (!container) return;
  if (!container.dataset.loaded) {
    container.innerHTML = `<div class="activity-empty">Loading…</div>`;
  }
  try {
    /** @type {[ShareInfo, DevicesPayload]} */
    const [info, devices] = await Promise.all([
      apiFetch("/api/v1/share/info"),
      apiFetch("/api/v1/share/devices"),
    ]);
    container.innerHTML = _shareTabHTML(info, devices);
    container.dataset.loaded = "1";
  } catch (e) {
    container.innerHTML = `<div class="activity-empty">Failed to load share info: ${esc(String(e))}</div>`;
  }
  // Auto-poll while the Share tab pane is visible — only re-renders
  // the Devices section, leaving the URL + QR untouched (otherwise the
  // QR <img> reloads every 3s and visibly flickers).
  if (_shareTabPollTimer) clearInterval(_shareTabPollTimer);
  _shareTabPollTimer = setInterval(() => {
    const pane = document.getElementById("settings-pane-share");
    if (!pane || !pane.classList.contains("active")) {
      if (_shareTabPollTimer) clearInterval(_shareTabPollTimer);
      _shareTabPollTimer = null;
      return;
    }
    _refreshDevicesSection();
  }, 3000);
}

/**
 * Refresh just the Devices section in place — used by the auto-poll
 * so the QR <img> doesn't get re-fetched (which causes a visible
 * flicker on every poll tick).
 */
async function _refreshDevicesSection() {
  const target = document.getElementById("share-devices-section");
  if (!target) return; // tab not in devices-rendering state
  try {
    /** @type {DevicesPayload} */
    const devices = await apiFetch("/api/v1/share/devices");
    target.innerHTML = _devicesHTML(devices);
  } catch {
    /* swallow — next tick will retry */
  }
}

/**
 * @param {ShareInfo} info
 * @param {DevicesPayload} [devices]
 * @returns {string}
 */
function _shareTabHTML(info, devices) {
  const toggle = `
    <div class="setting-item">
      <div class="settings-row" style="justify-content:space-between">
        <div>
          <div><strong>LAN sharing</strong></div>
          <div style="color:var(--text-secondary);font-size:0.88em;margin-top:2px">
            Allow other devices on this Wi-Fi to open the library.
          </div>
        </div>
        <label class="setting-toggle">
          <input type="checkbox" id="share-toggle" ${info.enabled ? "checked" : ""}
                 data-onchange="_shareToggle">
          <span class="toggle-track"></span>
        </label>
      </div>
    </div>
  `;

  if (!info.enabled) {
    const ipHint = info.lan_ip
      ? `Your LAN IP is <code>${esc(info.lan_ip)}</code>.`
      : `No LAN-routable network detected — connect to Wi-Fi first.`;
    return `
      ${toggle}
      <div class="setting-item">
        <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:6px;color:var(--text-secondary);font-size:0.9em">
          <div>${ipHint}</div>
          <div style="margin-top:4px">⚠️ Anyone on your network with the URL can browse your photos. Only enable on networks you trust.</div>
        </div>
      </div>
    `;
  }

  const url = info.share_url || "";
  const qrSrc = authedSrc("/api/v1/share/qr") + "&_cb=" + Date.now();
  return `
    ${toggle}
    <div class="setting-item">
      <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:12px">
        <div style="width:100%">
          <label style="font-size:0.88em;color:var(--text-secondary);display:block;margin-bottom:4px">Share URL (includes auth token)</label>
          <div style="display:flex;gap:8px;align-items:center">
            <input id="share-url-input" type="text" readonly value="${escapeAttr(url)}"
              style="flex:1;padding:8px 12px;border-radius:6px;background:var(--bg-secondary);border:1px solid var(--border);color:var(--text-primary);font-family:monospace;font-size:0.85em"
              data-action="_bppThisSelect">
            <button class="preset-btn save" data-action="_copyShareUrl">Copy</button>
            <button class="preset-btn" data-action="_revokeShareLink" title="Generate a fresh URL — old URL stops working">Revoke</button>
          </div>
        </div>

        <div style="display:flex;flex-direction:column;align-items:center;gap:6px;width:100%">
          <div style="font-size:0.72em;color:var(--text-secondary);font-weight:600;letter-spacing:0.06em;text-transform:uppercase">Scan to open</div>
          <div style="background:#fff;padding:10px;border-radius:10px;line-height:0;box-shadow:0 4px 12px rgba(0,0,0,0.25)">
            <img src="${escapeAttr(qrSrc)}" alt="Scan to open share URL" width="200" height="200" style="display:block">
          </div>
          <div style="font-size:0.72em;color:var(--text-secondary);margin-top:2px">Point a phone camera at the QR · token survives restarts · ⚠️ trusted networks only</div>
        </div>
      </div>
    </div>
    <div id="share-devices-section">${_devicesHTML(devices || { pending: [], trusted: [] })}</div>
    ${_recentAccessHTML(info.recent_access || [])}
  `;
}

/**
 * @param {DevicesPayload} devices
 */
function _devicesHTML(devices) {
  const pending = devices.pending || [];
  const trusted = devices.trusted || [];

  const pendingHTML = pending.length
    ? pending
        .map(
          (d) => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);gap:12px">
          <div style="flex:1;min-width:0">
            <div style="font-weight:500">${esc(d.name || "Unknown device")}${d.prev_revoked ? `<span style="color:#f59e0b;font-size:0.78em;margin-left:8px;font-weight:normal">⚠ previously revoked</span>` : ""}</div>
            <div style="color:var(--text-secondary);font-size:0.82em">${esc(d.ip_at_pair)} · ${esc(_relTime(d.first_seen))}</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="preset-btn save" data-action="_approveDevice" data-arg0="${d.id}" title="Approve this device">Approve</button>
            <button class="preset-btn" data-action="_blockDevice" data-arg0="${d.id}" title="Block this device">Block</button>
          </div>
        </div>
      `
        )
        .join("")
    : "";

  const trustedHTML = trusted.length
    ? trusted
        .map(
          (d) => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);gap:12px">
          <div style="flex:1;min-width:0">
            <div style="font-weight:500">${esc(d.name || "Unknown device")}${d.prev_revoked ? `<span style="color:var(--text-secondary);font-size:0.78em;margin-left:8px;font-weight:normal">re-approved after revoke</span>` : ""}</div>
            <div style="color:var(--text-secondary);font-size:0.82em">${esc(d.ip_at_pair)} · last seen ${esc(_relTime(d.last_seen))}</div>
          </div>
          <button class="preset-btn" data-action="_revokeDevice" data-arg0="${d.id}" title="Revoke access for this device">Revoke</button>
        </div>
      `
        )
        .join("")
    : `<div style="color:var(--text-secondary);font-size:0.88em;padding:6px 0">No trusted devices yet.</div>`;

  return `
    ${
      pending.length
        ? `<div class="setting-item">
        <div class="settings-row" style="flex-direction:column;align-items:stretch;gap:6px">
          <div><strong>Pending requests</strong>
            <span style="background:#f59e0b;color:#000;font-size:0.7em;padding:2px 6px;border-radius:8px;margin-left:6px;font-weight:600">${pending.length} waiting</span>
          </div>
          <div>${pendingHTML}</div>
        </div>
      </div>`
        : ""
    }
    <div class="setting-item">
      <div class="settings-row" style="flex-direction:column;align-items:stretch;gap:6px">
        <div><strong>Trusted devices</strong></div>
        <div>${trustedHTML}</div>
      </div>
    </div>
  `;
}

export async function _approveDevice(id) {
  try {
    await apiFetch(`/api/v1/share/devices/${id}/approve`, { method: "POST" });
    toast("Device approved");
    await _renderShareTab();
  } catch (e) {
    toastError("approve the device", e);
  }
}

export async function _revokeDevice(id) {
  const ok = await appConfirm(
    "Revoke this device?",
    "It will lose access immediately and see an 'Access revoked' page. They can scan the QR again to request access — you'll see them back in Pending requests.",
    { okLabel: "Revoke" }
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/share/devices/${id}/revoke`, { method: "POST" });
    toast("Device revoked");
    await _renderShareTab();
  } catch (e) {
    toastError("revoke access", e);
  }
}

/**
 * Reject a *pending* device. Same DB op as revoke, but the confirm
 * copy is framed for "never approved them in the first place" rather
 * than "kicking out a trusted device".
 */
export async function _blockDevice(id) {
  const ok = await appConfirm(
    "Block this request?",
    "The device won't be approved. They'll see an 'Access revoked' page. If they re-scan, you'll see them in Pending requests again with a 'previously revoked' tag.",
    { okLabel: "Block" }
  );
  if (!ok) return;
  try {
    await apiFetch(`/api/v1/share/devices/${id}/revoke`, { method: "POST" });
    toast("Device blocked");
    await _renderShareTab();
  } catch (e) {
    toastError("block the device", e);
  }
}

/**
 * @param {ShareAccess[]} entries
 */
function _recentAccessHTML(entries) {
  if (!entries.length) {
    return `
      <div class="setting-item">
        <div class="settings-row" style="flex-direction:column;align-items:flex-start;gap:4px">
          <div><strong>Recent access</strong></div>
          <div style="color:var(--text-secondary);font-size:0.88em">No share-link access yet.</div>
        </div>
      </div>
    `;
  }
  const rows = entries
    .slice(0, 10)
    .map(
      (e) => `
        <div style="display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--border);font-size:0.88em">
          <div style="font-family:monospace">${esc(e.ip)}</div>
          <div style="color:var(--text-secondary);flex:1;text-align:left;padding:0 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeAttr(e.user_agent || "")}">${esc(_shortUserAgent(e.user_agent || "Unknown"))}</div>
          <div style="color:var(--text-secondary);white-space:nowrap">${esc(_relTime(e.ts))}</div>
        </div>
      `
    )
    .join("");
  return `
    <div class="setting-item">
      <div class="settings-row" style="flex-direction:column;align-items:stretch;gap:6px">
        <div><strong>Recent access</strong>
          <span style="color:var(--text-secondary);font-size:0.85em;font-weight:normal">— last ${entries.length}</span>
        </div>
        <div style="margin-top:4px">${rows}</div>
        <div style="color:var(--text-secondary);font-size:0.82em;margin-top:6px">
          See something you don't recognize? Click <strong>Revoke</strong> above to kill the current URL.
        </div>
      </div>
    </div>
  `;
}

/**
 * @param {string} ua
 */
function _shortUserAgent(ua) {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  if (/Linux/.test(ua)) return "Linux";
  return ua.slice(0, 32);
}

/**
 * @param {number} ts - Unix seconds.
 */
function _relTime(ts) {
  const sec = Math.floor(Date.now() / 1000) - ts;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

/**
 * @param {boolean} enabled
 */
export async function _shareToggle(enabled) {
  try {
    await apiFetch("/api/v1/share/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    await _renderShareTab();
    toast(enabled ? "LAN sharing enabled" : "LAN sharing disabled");
  } catch (e) {
    toastError("update sharing", e);
    await _renderShareTab(); // re-sync the toggle state from server
  }
}

export async function _revokeShareLink() {
  const ok = await appConfirm(
    "Revoke share link?",
    "The current URL will stop working immediately. Anyone using it will lose access. You'll get a fresh URL.",
    { okLabel: "Revoke" }
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/share/revoke", { method: "POST" });
    await _renderShareTab();
    toast("Share link revoked — new URL ready");
  } catch (e) {
    toastError("revoke access", e);
  }
}

export async function _copyShareUrl() {
  const input = /** @type {HTMLInputElement|null} */ (document.getElementById("share-url-input"));
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
    toast("Share URL copied");
  } catch {
    input.select();
    document.execCommand("copy");
    toast("Share URL copied");
  }
}
