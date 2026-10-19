// @ts-check
// The install prompts (face recognition before analysis, AI removal in the
// editor): what they offer, and what they say while an install waits.
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", async (importOriginal) => ({
  .../** @type {object} */ (await importOriginal()),
  appConfirm: vi.fn(async () => true),
}));

import { _promptFaceInstall } from "../bpp/web/static/js/modules/analysis-install.mjs";
import { installAllowedNow, showInstallDone } from "../bpp/web/static/js/modules/install-done.mjs";
import { _installInpaintModel } from "../bpp/web/static/js/modules/editor-inpaint.mjs";
import { restartBanner, stubState, toasts, useInstallDom } from "./_install-helpers.mjs";

useInstallDom();

describe("face install prompt", () => {
  afterEach(() => {
    delete (/** @type {any} */ (window).faceInstallable);
    delete (/** @type {any} */ (window).installRestartPending);
  });

  it("says to restart instead of offering an install while one is pending", () => {
    document.body.innerHTML += '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).faceInstallable = false;
    /** @type {any} */ (window).installRestartPending = ["numpy"];
    void _promptFaceInstall();
    const text = document.getElementById("confirm-overlay")?.textContent || "";
    expect(text).toContain("Restart Best Photo Picker to finish a recent install");
    expect(text).not.toContain("Install faces");
  });
});

describe("face install prompt wording", () => {
  it("says face recognition itself only needs the restart", () => {
    document.body.innerHTML += '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).faceInstallable = false;
    /** @type {any} */ (window).installRestartPending = ["numpy"];
    /** @type {any} */ (window).faceWaitingForRestart = true;
    void _promptFaceInstall();
    const text = document.getElementById("confirm-overlay")?.textContent || "";
    expect(text).toContain("Restart Best Photo Picker to finish installing face recognition");
  });
});

describe("an install the server would refuse right now", () => {
  afterEach(() => {
    delete (/** @type {any} */ (window).installBlocked);
  });

  it("is refused before the consent dialog, with the reason", async () => {
    stubState({
      restart_pending: [],
      install_blocked: "Wait for analysis to finish (or stop it), then install",
    });
    expect(await installAllowedNow()).toBe(false);
    expect(toasts()[0]).toContain("Can't install yet: Wait for analysis to finish");
  });

  it("the face prompt says why instead of 'pip install'", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).installBlocked = "Wait for <b>my_plugin</b> to finish";
    void _promptFaceInstall();
    const html = document.getElementById("confirm-overlay")?.innerHTML || "";
    expect(html).toContain("can't be installed right now: Wait for &lt;b&gt;my_plugin&lt;/b&gt;");
  });
});

describe("face install prompt while analysis waits for a restart", () => {
  it("knows right after a smart-search install, before any refresh", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    showInstallDone("Smart search", "onnxruntime");
    void _promptFaceInstall();
    expect(document.getElementById("confirm-overlay")?.textContent).not.toContain("Skip faces");
  });

  afterEach(() => {
    delete (/** @type {any} */ (window).installRestartPending);
  });

  it("offers no 'Skip faces' the server would refuse", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).installRestartPending = ["faces"];
    void _promptFaceInstall();
    expect(document.getElementById("confirm-overlay")?.textContent).not.toContain("Skip faces");
  });

  it("hides it after any install waiting for a restart (even AI removal)", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).installRestartPending = ["inpaint"];
    void _promptFaceInstall();
    expect(document.getElementById("confirm-overlay")?.textContent).not.toContain("Skip faces");
  });

  it("offers it when nothing waits", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).installRestartPending = [];
    void _promptFaceInstall();
    expect(document.getElementById("confirm-overlay")?.textContent).toContain("Skip faces");
  });
});

describe("AI removal install that fails but needs a restart", () => {
  afterEach(() => {
    delete (/** @type {any} */ (window)._inpaintAvailable);
    delete (/** @type {any} */ (window)._inpaintRestartPending);
  });

  it("a click refused because AI removal itself waits shows the restart notice", async () => {
    document.body.innerHTML += '<div id="inpaint-status"></div>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    stubState({ restart_pending: ["inpaint"] });
    await _installInpaintModel(document.createElement("button"));
    const status = document.getElementById("inpaint-status")?.textContent || "";
    expect(status).toContain("Restart Best Photo Picker");
    expect(status).not.toContain("Download AI Model");
  });

  it("another install waiting for a restart doesn't make AI removal 'installed'", async () => {
    document.body.innerHTML += '<div id="inpaint-status"></div>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */ (window)._inpaintRestartPending = false;
    stubState({ restart_pending: ["faces"] });
    await _installInpaintModel(document.createElement("button"));
    expect(/** @type {any} */ (window)._inpaintRestartPending).toBe(false);
  });

  it("a face prompt for an installed but unaccepted face model points at the license", () => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).faceLicenseNeeded = true;
    void _promptFaceInstall();
    expect(document.getElementById("confirm-overlay")?.textContent).toContain(
      "license isn't accepted"
    );
    delete (/** @type {any} */ (window).faceLicenseNeeded);
  });

  it("a POST refused because another feature waits keeps AI removal uninstalled", async () => {
    document.body.innerHTML += '<div id="inpaint-status"></div>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */ (window)._inpaintRestartPending = false;
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        // 1: the pre-check (nothing waits yet) · 2: the POST, refused (faces
        // was installed in another tab meanwhile) · 3: the state refresh.
        const body =
          calls === 2
            ? {
                error: "Restart Best Photo Picker to finish the last install first",
                context: { reason: "restart_pending" },
              }
            : { restart_pending: calls === 1 ? [] : ["faces"] };
        return new Response(JSON.stringify(body), {
          status: calls === 2 ? 409 : 200,
          headers: { "content-type": "application/json" },
        });
      })
    );
    const btn = document.createElement("button");
    await _installInpaintModel(btn);
    expect(/** @type {any} */ (window)._inpaintRestartPending).toBe(false);
    expect(btn.disabled).toBe(false);
  });

  it("a click refused only for now (analysis running) keeps the button", async () => {
    document.body.innerHTML += '<div id="inpaint-status"></div>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */ (window)._inpaintRestartPending = false;
    stubState({ restart_pending: [], install_blocked: "Wait for analysis to finish" });
    await _installInpaintModel(document.createElement("button"));
    expect(/** @type {any} */ (window)._inpaintRestartPending).toBe(false);
    expect(toasts()[0]).toContain("Can't install yet");
  });

  it("asks the server again when pip found it installed already", async () => {
    document.body.innerHTML +=
      '<div id="inpaint-status"></div><button id="inpaint-apply-btn" disabled></button>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */
    let source = null;
    vi.stubGlobal(
      "EventSource",
      vi.fn(function FakeEventSource() {
        source = this;
        this.close = vi.fn();
      })
    );
    stubState({ restart_pending: [], status: "started", available: true });
    const done = _installInpaintModel(document.createElement("button"));
    await vi.waitFor(() => expect(source).not.toBeNull());
    source.onmessage({ data: JSON.stringify({ type: "done", restart_required: false }) });
    await done;
    expect(/** @type {any} */ (window)._inpaintAvailable).toBe(true);
    expect(
      /** @type {HTMLButtonElement} */ (document.getElementById("inpaint-apply-btn")).disabled
    ).toBe(false);
  });

  it("shows the restart notice where the download button was", async () => {
    document.body.innerHTML += '<div id="inpaint-status"></div>';
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */
    let source = null;
    vi.stubGlobal(
      "EventSource",
      vi.fn(function FakeEventSource() {
        source = this;
        this.close = vi.fn();
      })
    );
    stubState({ restart_pending: [], status: "started" });
    const btn = document.createElement("button");
    const done = _installInpaintModel(btn);
    await vi.waitFor(() => expect(source).not.toBeNull());
    source.onmessage({
      data: JSON.stringify({
        type: "error",
        message: "pip exited with code 1",
        restart_required: true,
      }),
    });
    await done;
    const status = document.getElementById("inpaint-status")?.textContent || "";
    expect(status).toContain("Restart Best Photo Picker to finish installing AI removal");
    expect(restartBanner()).toContain("Restart Best Photo Picker");
  });
});
