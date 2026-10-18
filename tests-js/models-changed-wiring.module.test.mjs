// @ts-check
// Every successful license / model-file change in the Settings → Models
// picker fires "bpp:models-changed" (the picker reloads and the content-
// filter hint refreshes on it); the picker reloads when it fires; and a
// model accepted without commercial-use rights opens the dialog ready for
// input, not in read-only "already accepted" review mode.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetchMock = vi.fn().mockResolvedValue({});
vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: apiFetchMock,
  authedSrc: (/** @type {string} */ p) => p,
}));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({
  appConfirm: vi.fn().mockResolvedValue(true),
}));

const fe = await import("../bpp/web/static/js/modules/modals-face-embedders.mjs");
const { MODELS_CHANGED_EVENT, notifyModelsChanged } =
  await import("../bpp/web/static/js/modules/models-changed.mjs");
const { feState } = await import("../bpp/web/static/js/modules/face-embedders-state.mjs");
const { setFaceEmbedderUseContext } =
  await import("../bpp/web/static/js/modules/face-embedders-actions.mjs");

/** @type {any} */
let heard;

beforeEach(() => {
  document.body.innerHTML = `
    <div id="face-embedder-picker"></div>
    <div id="toast-container"></div>
    <div class="modal-overlay" id="fe-acceptance-overlay" style="display:none">
      <div class="modal fe-acceptance-modal"><div id="fe-acceptance-body"></div></div>
    </div>`;
  fe._resetFaceEmbedderStateForTests();
  apiFetchMock.mockReset();
  apiFetchMock.mockResolvedValue({});
  heard = vi.fn();
  document.addEventListener(MODELS_CHANGED_EVENT, heard);
});

afterEach(() => {
  document.removeEventListener(MODELS_CHANGED_EVENT, heard);
  document.body.innerHTML = "";
});

describe("success paths announce the change", () => {
  test("withdrawing an acceptance", async () => {
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["insightface_buffalo_s"] });
    await fe.revokeFaceEmbedderAcceptance("insightface_buffalo_s");
    expect(heard).toHaveBeenCalledTimes(1);
  });

  test("uninstalling a model", async () => {
    fe._seedFaceEmbedderStateForTests({
      installState: { opencv_yunet: { files: [{ name: "yunet.onnx", exists: true }] } },
    });
    await fe.uninstallFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    expect(heard).toHaveBeenCalledTimes(1);
  });

  test("switching the use context", async () => {
    await setFaceEmbedderUseContext("commercial");
    expect(heard).toHaveBeenCalledTimes(1);
  });

  test("a failed action reloads the picker but announces nothing", async () => {
    fe._seedFaceEmbedderStateForTests({
      installState: { opencv_yunet: { files: [{ name: "yunet.onnx", exists: true }] } },
    });
    apiFetchMock.mockRejectedValue(new Error("offline"));
    await fe.uninstallFaceEmbedderEntry("opencv_yunet", "OpenCV YuNet");
    expect(heard).not.toHaveBeenCalled();
  });
});

describe("the picker reloads on the signal", () => {
  test("while Settings is open", async () => {
    document.body.innerHTML += '<div id="settings-overlay" class="visible"></div>';
    apiFetchMock.mockResolvedValue({ groups: [] });
    notifyModelsChanged();
    await vi.waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/api/v1/model-registry/entries")
    );
  });

  test("not while Settings is closed", async () => {
    document.body.innerHTML += '<div id="settings-overlay"></div>';
    notifyModelsChanged();
    await new Promise((r) => setTimeout(r, 0));
    expect(apiFetchMock).not.toHaveBeenCalledWith("/api/v1/model-registry/entries");
  });
});

describe("commercial-use rights", () => {
  test("the picker keeps the acceptance and records the row's load state", async () => {
    document.body.innerHTML += '<div id="settings-overlay" class="visible"></div>';
    apiFetchMock.mockImplementation(async (/** @type {string} */ url) => {
      if (url === "/api/v1/model-registry/entries") {
        return {
          groups: [
            {
              title: "Restricted",
              entries: [
                {
                  id: "nudenet_320n",
                  display_name: "NudeNet",
                  requires_explicit_ack: true,
                  load_state: "needs_commercial_rights",
                },
              ],
            },
          ],
        };
      }
      if (url === "/api/v1/model-registry/acceptance/list") {
        return { acceptances: [{ model_id: "nudenet_320n", event: "accept", current: true }] };
      }
      return {};
    });
    await fe.loadFaceEmbedderPicker();
    expect(feState.acceptedIds.has("nudenet_320n")).toBe(true);
    expect(feState.loadStates.get("nudenet_320n")).toBe("needs_commercial_rights");
  });
});

describe("license dialog modes", () => {
  const draft = {
    model_id: "nudenet_320n",
    model_display_name: "NudeNet",
    required_checkboxes: [{ id: "box", text: "I agree" }],
    separate_rights_assertion: "You hold separate rights.",
  };
  const accept = () => /** @type {HTMLButtonElement} */ (document.getElementById("fe-accept-btn"));

  beforeEach(() => {
    document.body.innerHTML += `
      <button id="fe-accept-btn" class="modal-btn-primary"></button>
      <button data-action="closeFaceEmbedderAcceptance">Cancel</button>`;
    apiFetchMock.mockResolvedValue(draft);
  });

  test("view-only (withdrawn, never accepted): Close only, nothing to submit or withdraw", async () => {
    await fe.openFaceEmbedderAcceptance("nudenet_320n", "view");
    const boxes = document.querySelectorAll("#fe-acceptance-body input[type=checkbox]");
    for (const b of boxes) expect(/** @type {HTMLInputElement} */ (b).disabled).toBe(true);
    expect(accept().hidden).toBe(true);
    expect(document.querySelector('[data-action="revokeFaceEmbedderAcceptance"]')).toBeNull();
    expect(document.querySelector('[data-action="closeFaceEmbedderAcceptance"]')?.textContent).toBe(
      "Close"
    );
  });

  test("missing commercial rights: asks for them, keeps withdraw available", async () => {
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["nudenet_320n"], useContext: "commercial" });
    feState.loadStates = new Map([["nudenet_320n", "needs_commercial_rights"]]);
    await fe.openFaceEmbedderAcceptance("nudenet_320n");
    const rights = /** @type {HTMLInputElement} */ (document.getElementById("fe-separate-rights"));
    expect(rights.disabled).toBe(false);
    expect(rights.checked).toBe(false);
    expect(accept().hidden).toBe(false);
    expect(accept().dataset.action).toBe("confirmFaceEmbedderAcceptance");
    const withdraw = /** @type {HTMLElement} */ (
      document.querySelector('#fe-acceptance-body [data-action="revokeFaceEmbedderAcceptance"]')
    );
    expect(withdraw.dataset.arg0).toBe("nudenet_320n");
  });

  test("a view-only open never leaks into the next dialog", async () => {
    await fe.openFaceEmbedderAcceptance("nudenet_320n", "view");
    expect(accept().hidden).toBe(true);
    await fe.openFaceEmbedderAcceptance("nudenet_320n");
    expect(accept().hidden).toBe(false);
    expect(accept().dataset.action).toBe("confirmFaceEmbedderAcceptance");
  });

  test("view-only for a model you'd accepted keeps a way to withdraw", async () => {
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["nudenet_320n"] });
    await fe.openFaceEmbedderAcceptance("nudenet_320n", "view");
    expect(
      document.querySelector('#fe-acceptance-body [data-action="revokeFaceEmbedderAcceptance"]')
    ).not.toBeNull();
  });

  test("the rights state without an acceptance on file opens a fresh acceptance", async () => {
    feState.loadStates = new Map([["nudenet_320n", "needs_commercial_rights"]]);
    await fe.openFaceEmbedderAcceptance("nudenet_320n");
    const box = /** @type {HTMLInputElement} */ (
      document.querySelector("#fe-acceptance-body input[data-checkbox-id]")
    );
    expect(box.checked).toBe(false);
    expect(box.disabled).toBe(false);
  });

  test("rights mode: I accept waits for the rights box, then sends it", async () => {
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["nudenet_320n"], useContext: "commercial" });
    feState.loadStates = new Map([["nudenet_320n", "needs_commercial_rights"]]);
    await fe.openFaceEmbedderAcceptance("nudenet_320n");
    expect(accept().disabled).toBe(true);
    const rights = /** @type {HTMLInputElement} */ (document.getElementById("fe-separate-rights"));
    rights.checked = true;
    rights.dispatchEvent(new Event("change", { bubbles: true }));
    expect(accept().disabled).toBe(false);
    apiFetchMock.mockClear();
    apiFetchMock.mockResolvedValue({});
    await fe.confirmFaceEmbedderAcceptance();
    const post = apiFetchMock.mock.calls.find(
      (c) => c[0] === "/api/v1/model-registry/acceptance/confirm"
    );
    const body = JSON.parse(post[1].body);
    expect(body.separate_rights_asserted).toBe(true);
    expect(body.checkbox_responses).toEqual({ box: true });
    expect(heard).toHaveBeenCalledTimes(1); // accepting announces the change
  });

  test("withdrawing replaces the row's cached load state: it needs a license", async () => {
    // (Not the stale "needs commercial rights": the dialog asks to accept.)
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["nudenet_320n"] });
    feState.loadStates = new Map([["nudenet_320n", "needs_commercial_rights"]]);
    apiFetchMock.mockResolvedValue({});
    await fe.revokeFaceEmbedderAcceptance("nudenet_320n");
    expect(feState.loadStates.get("nudenet_320n")).toBe("needs_license");
  });

  test("accepted and fine: review with Withdraw", async () => {
    fe._seedFaceEmbedderStateForTests({ acceptedIds: ["nudenet_320n"] });
    await fe.openFaceEmbedderAcceptance("nudenet_320n");
    expect(accept().hidden).toBe(false);
    expect(accept().dataset.action).toBe("revokeFaceEmbedderAcceptance");
  });
});

describe("accepting or withdrawing a license updates the row at once", () => {
  test("accepted: it may load, even before (or without) a successful reload", async () => {
    feState.loadStates.set("nudenet_320n", "needs_license");
    feState.currentDraft = {
      model_id: "nudenet_320n",
      model_display_name: "NudeNet",
      required_checkboxes: [],
    };
    document.body.innerHTML += '<input type="checkbox" id="fe-ack-dummy" checked>';
    await fe.confirmFaceEmbedderAcceptance();
    expect(feState.loadStates.get("nudenet_320n")).toBe("ok");
  });
});
