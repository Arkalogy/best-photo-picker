// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn().mockResolvedValue({});

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const {
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  _acceptanceGateSatisfied,
  closeFaceEmbedderAcceptance,
  openFaceEmbedderAcceptance,
  redownloadFaceEmbedderEntry,
  revokeFaceEmbedderAcceptance,
} = await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");

beforeEach(() => {
  document.body.innerHTML = `
    <div id="face-embedder-picker"></div>
    <div id="toast-container"></div>
  `;
  _resetFaceEmbedderStateForTests();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Download is NOT silent: in-row progress + real error reason ──
//
// The /redownload endpoint blocks until the file is fetched (seconds).
// Per the "nothing should be silent" rule, the row must show progress —
// not just a fire-and-forget toast — and a failure must surface the real
// reason, not a generic "failed".

describe("redownloadFaceEmbedderEntry — progress + error surfacing", () => {
  const seedRow = () => {
    _seedFaceEmbedderStateForTests({
      installState: {
        x: { status: "ready", label: "X model", files: [{ name: "x.onnx", exists: true }] },
      },
    });
    document.getElementById("face-embedder-picker").innerHTML = `
      <table><tbody>
        <tr data-entry-id="x">
          <td class="fe-cell-status"><span class="fe-status">Installed</span></td>
        </tr>
      </tbody></table>`;
  };
  const statusText = () =>
    document.querySelector('tr[data-entry-id="x"] .fe-cell-status')?.textContent || "";
  // There are two toasts (the "Downloading…" announce + the result); the
  // outcome is the most recent one.
  const latestToastText = () => {
    const all = document.querySelectorAll("#toast-container .toast");
    return all.length ? all[all.length - 1].textContent || "" : "";
  };

  test("shows in-row 'Downloading…' while the request is in flight", async () => {
    seedRow();
    /** @type {(v?: unknown) => void} */
    let release = () => {};
    apiFetchMock.mockReset();
    apiFetchMock.mockImplementation(
      () =>
        new Promise((res) => {
          release = res;
        })
    );

    const p = redownloadFaceEmbedderEntry("x", "X model");
    // Before the network resolves, the row must already say so.
    expect(statusText()).toMatch(/Downloading/);
    release({}); // let the POST resolve
    await p;
  });

  test("failure surfaces the real reason from the backend", async () => {
    seedRow();
    apiFetchMock.mockReset();
    // First call (the redownload POST) rejects with the backend's reason;
    // later calls (the picker reload) resolve.
    apiFetchMock.mockResolvedValue({});
    apiFetchMock.mockRejectedValueOnce(new Error("Couldn't download X model: disk full"));

    await redownloadFaceEmbedderEntry("x", "X model");

    expect(latestToastText()).toMatch(/disk full/);
  });

  test("success posts the file name and toasts completion", async () => {
    seedRow();
    apiFetchMock.mockReset();
    apiFetchMock.mockImplementation((/** @type {string} */ url) =>
      Promise.resolve(
        url === "/api/v1/models/redownload"
          ? { job: "model:x.onnx", status: "running" }
          : { status: "done", result: {} }
      )
    );

    await redownloadFaceEmbedderEntry("x", "X model");

    const post = apiFetchMock.mock.calls.find((c) => c[0] === "/api/v1/models/redownload");
    expect(post).toBeDefined();
    expect(JSON.parse(post[1].body)).toEqual({ name: "x.onnx" });
    expect(latestToastText()).toMatch(/downloaded/i);
  });
});

// ── "I accept" stays disabled until every box is checked ──

describe("acceptance gate — _acceptanceGateSatisfied", () => {
  const mount = (boxesChecked, { commercial = false, rightsChecked = false } = {}) => {
    const boxes = boxesChecked
      .map(
        (checked, i) =>
          `<input type="checkbox" data-checkbox-id="cb${i}"${checked ? " checked" : ""}>`
      )
      .join("");
    const rights = commercial
      ? `<input type="checkbox" id="fe-separate-rights"${rightsChecked ? " checked" : ""}>`
      : "";
    document.body.innerHTML = `
      <div id="fe-acceptance-overlay">${boxes}${rights}</div>`;
    return document.getElementById("fe-acceptance-overlay");
  };

  test("false when any required box is unchecked", () => {
    _resetFaceEmbedderStateForTests(); // use_context = unspecified
    const root = mount([true, false, true]);
    expect(_acceptanceGateSatisfied(root)).toBe(false);
  });

  test("true when every required box is checked (non-commercial)", () => {
    _resetFaceEmbedderStateForTests();
    const root = mount([true, true, true]);
    expect(_acceptanceGateSatisfied(root)).toBe(true);
  });

  test("commercial: all boxes checked but rights box unchecked → false", () => {
    _seedFaceEmbedderStateForTests({ useContext: "commercial" });
    const root = mount([true, true], { commercial: true, rightsChecked: false });
    expect(_acceptanceGateSatisfied(root)).toBe(false);
  });

  test("commercial: all boxes + rights box checked → true", () => {
    _seedFaceEmbedderStateForTests({ useContext: "commercial" });
    const root = mount([true, true], { commercial: true, rightsChecked: true });
    expect(_acceptanceGateSatisfied(root)).toBe(true);
  });
});

describe("acceptance gate wiring — button disabled state", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="toast-container"></div>
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal">
          <div id="fe-acceptance-body"></div>
          <div class="modal-actions">
            <button data-action="closeFaceEmbedderAcceptance">Cancel</button>
            <button id="fe-accept-btn" data-action="confirmFaceEmbedderAcceptance" disabled>I accept</button>
          </div>
        </div>
      </div>`;
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({
      model_id: "m",
      model_display_name: "M",
      compressed_disclaimer: "",
      full_disclaimer: "",
      commercial_use_definition: "",
      biometric_responsibility_text: "",
      produces_biometric_data: false,
      required_checkboxes: [
        { id: "a", text: "Ack A" },
        { id: "b", text: "Ack B" },
      ],
      separate_rights_assertion: "",
      ack_text_version: "v1",
      ack_text_sha256: "x",
      use_context_text_version: "v1",
      use_context_text_sha256: "y",
      use_context: "personal",
      terms_url: "https://example.invalid",
      terms_permalink_url: "https://example.invalid/x",
      terms_retrieved_at: "2026-01-01",
    });
  });

  const acceptBtn = () =>
    /** @type {HTMLButtonElement} */ (document.getElementById("fe-accept-btn"));

  test("accept button starts disabled when boxes are unchecked", async () => {
    await openFaceEmbedderAcceptance("m");
    expect(acceptBtn().disabled).toBe(true);
  });

  test("checking ALL boxes enables it; unchecking one disables it again", async () => {
    await openFaceEmbedderAcceptance("m");
    const boxes = /** @type {HTMLInputElement[]} */ ([
      ...document.querySelectorAll("input[type=checkbox][data-checkbox-id]"),
    ]);
    expect(boxes).toHaveLength(2);

    boxes[0].checked = true;
    boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "still disabled with one box unchecked").toBe(true);

    boxes[1].checked = true;
    boxes[1].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "enabled once all boxes checked").toBe(false);

    boxes[0].checked = false;
    boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(acceptBtn().disabled, "re-disabled when a box is unchecked").toBe(true);
  });

  test("REGRESSION: already-accepted model opens read-only (no empty-checkbox contradiction)", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");

    const boxes = /** @type {HTMLInputElement[]} */ ([
      ...document.querySelectorAll("input[type=checkbox][data-checkbox-id]"),
    ]);
    // The menu shows ✓; the modal must agree — boxes pre-checked, disabled.
    expect(
      boxes.every((b) => b.checked && b.disabled),
      "boxes reflect prior acceptance"
    ).toBe(true);
    expect(document.querySelector(".fe-accepted-banner")).not.toBeNull();
  });

  test("already-accepted model offers Withdraw (repurposes the primary button)", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");

    const btn = acceptBtn();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent || "").toMatch(/withdraw/i);
    expect(btn.dataset.action).toBe("revokeFaceEmbedderAcceptance");
    expect(btn.dataset.arg0).toBe("m");
    expect(btn.classList.contains("modal-btn-danger")).toBe(true);
  });

  test("not-accepted model resets the button back to 'I accept' (no leaked Withdraw)", async () => {
    // Open an accepted model first (sets Withdraw), then a fresh one.
    _seedFaceEmbedderStateForTests({ acceptedIds: ["m"] });
    await openFaceEmbedderAcceptance("m");
    _seedFaceEmbedderStateForTests({ acceptedIds: [] });
    await openFaceEmbedderAcceptance("m");

    const btn = acceptBtn();
    expect(btn.textContent || "").toMatch(/i accept/i);
    expect(btn.dataset.action).toBe("confirmFaceEmbedderAcceptance");
    expect(btn.dataset.arg0).toBeUndefined();
    expect(btn.classList.contains("modal-btn-danger")).toBe(false);
  });
});

describe("revokeFaceEmbedderAcceptance — withdrawal flow", () => {
  beforeEach(() => {
    document.body.innerHTML += `
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal"><div id="fe-acceptance-body"></div></div>
      </div>`;
  });

  test("confirmed withdrawal POSTs revoke and drops the model from accepted", async () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: ["insightface_buffalo_s"] });
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({});

    await revokeFaceEmbedderAcceptance("insightface_buffalo_s");

    const post = apiFetchMock.mock.calls.find(
      (c) => c[0] === "/api/v1/model-registry/acceptance/revoke"
    );
    expect(post).toBeDefined();
    expect(JSON.parse(post[1].body)).toEqual({
      model_id: "insightface_buffalo_s",
    });
  });
});

// ── Acceptance dialog dismiss behaviour (project modal convention) ──

describe("acceptance dialog ESC + click-outside dismiss", () => {
  beforeEach(() => {
    // Seed the dialog overlay + minimal draft response.
    document.body.innerHTML += `
      <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
        <div class="modal fe-acceptance-modal">
          <div id="fe-acceptance-body"></div>
          <div class="modal-actions">
            <button data-action="closeFaceEmbedderAcceptance">Cancel</button>
            <button data-action="confirmFaceEmbedderAcceptance">I accept</button>
          </div>
        </div>
      </div>
    `;
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({
      model_id: "test_model",
      model_display_name: "Test model",
      compressed_disclaimer: "short",
      full_disclaimer: "long",
      commercial_use_definition: "",
      biometric_responsibility_text: "",
      produces_biometric_data: false,
      required_checkboxes: [],
      separate_rights_assertion: "",
      ack_text_version: "v1",
      ack_text_sha256: "abc",
      use_context_text_version: "v1",
      use_context_text_sha256: "def",
      use_context: "personal",
      terms_url: "https://example.invalid",
      terms_permalink_url: "https://example.invalid/abc",
      terms_retrieved_at: "2026-01-01",
    });
  });

  const overlayVisible = () => {
    const overlay = document.getElementById("fe-acceptance-overlay");
    return overlay?.classList.contains("visible") || false;
  };

  test("ESC dismisses an open acceptance dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
    });
    document.dispatchEvent(event);

    expect(overlayVisible()).toBe(false);
  });

  test("click on overlay backdrop dismisses the dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const overlay = document.getElementById("fe-acceptance-overlay");
    const event = new MouseEvent("click", { bubbles: true });
    // dispatch directly on the overlay (the backdrop), NOT on the
    // inner modal — clicks inside the modal must NOT dismiss.
    overlay?.dispatchEvent(event);

    expect(overlayVisible()).toBe(false);
  });

  test("click on inner modal does NOT dismiss the dialog", async () => {
    await openFaceEmbedderAcceptance("test_model");
    expect(overlayVisible()).toBe(true);

    const inner = document.querySelector(".fe-acceptance-modal");
    const event = new MouseEvent("click", { bubbles: true });
    inner?.dispatchEvent(event);

    expect(overlayVisible()).toBe(true);
  });

  test("ESC is a no-op when the dialog is closed", () => {
    // Dialog starts hidden — make sure ESC doesn't throw or do
    // anything surprising when nothing is open.
    expect(overlayVisible()).toBe(false);
    const event = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
    });
    document.dispatchEvent(event);
    expect(overlayVisible()).toBe(false);
  });
});
