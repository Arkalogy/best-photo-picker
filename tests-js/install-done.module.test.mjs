// @ts-check
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/dialogs.mjs", async (importOriginal) => ({
  .../** @type {object} */ (await importOriginal()),
  appConfirm: vi.fn(async () => true),
}));
vi.mock("../bpp/web/static/js/modules/modals-models-list.mjs", () => ({
  loadModelsList: vi.fn(),
}));

import { _promptFaceInstall } from "../bpp/web/static/js/modules/analysis-install.mjs";
import {
  applyInstallState,
  installAllowedNow,
  installDoneMessage,
  followInstall,
  installEventError,
  noteInstallError,
  refreshInstallState,
  showInstallDone,
  showInterruptedInstalls,
  toastInstallError,
} from "../bpp/web/static/js/modules/install-done.mjs";
import { installPackage } from "../bpp/web/static/js/modules/modals-models.mjs";
import { restartBanner, stubState, toasts, useInstallDom } from "./_install-helpers.mjs";

useInstallDom();

describe("installDoneMessage", () => {
  it("says the install works after a restart", () => {
    expect(installDoneMessage("X")).toBe("X installed — restart Best Photo Picker to use it");
  });
});

describe("showInstallDone", () => {
  it("asks for no restart when pip found it all installed already", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}"))
    );
    expect(showInstallDone("X", "faces", { restart_required: false })).toBe(false);
    expect(restartBanner()).toBe("");
    expect(toasts()[0]).toContain("X is already installed");
  });

  it("shows it as a warning and keeps the restart banner up", () => {
    showInstallDone("X", "onnxruntime");
    expect(restartBanner()).toContain("Restart Best Photo Picker");
    expect(/** @type {any} */ (window).faceInstallable).toBe(false);
    // Known at once: analysis (which loads it) is refused until the restart.
    expect(/** @type {any} */ (window).installRestartPending).toEqual(["onnxruntime"]);
    expect(toasts()).toEqual(["toast warning: X installed — restart Best Photo Picker to use it"]);
  });
});

describe("Settings → Models install", () => {
  it("tells the user to restart when the server says so", async () => {
    /** @type {any} */
    let source = null;
    vi.stubGlobal(
      "EventSource",
      vi.fn(function FakeEventSource() {
        source = this;
        this.close = vi.fn();
      })
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (/** @type {string} */ url) => {
        const body = url.endsWith("/info")
          ? { packages: ["onnxruntime"], host: "pypi.org" }
          : { status: "started" };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      })
    );
    const btn = document.createElement("button");
    const done = installPackage(btn, "onnxruntime", "ONNX Runtime");
    await vi.waitFor(() => expect(source).not.toBeNull());
    source.onmessage({
      data: JSON.stringify({
        type: "done",
        restart_required: true,
      }),
    });
    await done;
    expect(toasts()).toEqual([
      "toast warning: ONNX Runtime installed — restart Best Photo Picker to use it",
    ]);
  });
});

describe("toastInstallError", () => {
  it("shows 'restart first' as a warning, not a failure to retry", () => {
    const err = Object.assign(new Error("HTTP 409"), {
      status: 409,
      body: {
        error: "Restart Best Photo Picker to finish the last install first",
        code: "conflict",
        context: { reason: "restart_pending" },
      },
    });
    toastInstallError("install face recognition", err);
    expect(restartBanner()).toContain("Restart Best Photo Picker");
    expect(toasts()).toEqual([
      "toast warning: Couldn't install face recognition: Restart Best Photo Picker to finish the last install first.",
    ]);
  });

  it("a failed pip that needs a restart says restart, not just try again", () => {
    toastInstallError(
      "install ONNX Runtime",
      installEventError({ message: "pip exited with code 1", restart_required: true })
    );
    expect(toasts()).toEqual([
      "toast warning: Couldn't install ONNX Runtime: pip exited with code 1. " +
        "Restart Best Photo Picker, then try again.",
    ]);
  });

  it("shows any other error as a failure", () => {
    toastInstallError("install face recognition", new Error("pip missing"));
    expect(toasts()[0]).toContain("Couldn't install face recognition: pip missing");
  });
});

describe("install state from the server", () => {
  it("a pending restart found by the poll shows the banner", async () => {
    stubState({ restart_pending: ["numpy"] });
    expect(await refreshInstallState()).toBe(true);
    expect(restartBanner()).toContain("Restart Best Photo Picker");
    expect(/** @type {any} */ (window).installRestartPending).toEqual(["numpy"]);
  });

  it("nothing pending: no banner", async () => {
    stubState({ restart_pending: [] });
    expect(await refreshInstallState()).toBe(false);
    expect(restartBanner()).toBe("");
  });

  it("a failed poll is quiet and says nothing is pending", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    expect(await refreshInstallState()).toBe(false);
    expect(toasts()).toEqual([]);
  });

  it("refuses before the consent dialog while a restart is pending", async () => {
    stubState({ restart_pending: ["numpy"] });
    expect(await installAllowedNow()).toBe(false);
    expect(toasts()).toEqual([
      "toast warning: Restart Best Photo Picker to finish the last install first.",
    ]);
  });

  it("clears the banner once the app has been restarted", async () => {
    stubState({ restart_pending: ["faces"], face_installable: false });
    await refreshInstallState();
    expect(restartBanner()).toContain("Restart");
    expect(/** @type {any} */ (window).faceWaitingForRestart).toBe(true);
    stubState({ restart_pending: [], face_installable: true });
    await refreshInstallState();
    expect(restartBanner()).toBe("");
    expect(/** @type {any} */ (window).installRestartPending).toEqual([]);
    expect(/** @type {any} */ (window).faceInstallable).toBe(true);
    expect(/** @type {any} */ (window).faceWaitingForRestart).toBe(false);
  });

  it("shows what the page's first status says without asking again", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    expect(applyInstallState({ restart_pending: ["faces"], interrupted_keys: [] })).toBe(true);
    expect(restartBanner()).toContain("Restart");
    expect(/** @type {any} */ (window).faceWaitingForRestart).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("'wait for analysis' is a warning, not a failure", () => {
    toastInstallError(
      "install face recognition",
      Object.assign(new Error("HTTP 409"), {
        body: {
          error: "Wait for analysis to finish (or stop it), then install",
          context: { reason: "busy" },
        },
      })
    );
    expect(toasts()).toEqual([
      "toast warning: Couldn't install face recognition yet: Wait for analysis to finish (or stop it), then install.",
    ]);
    expect(restartBanner()).toBe("");
  });

  it("lets the install go ahead otherwise", async () => {
    stubState({ restart_pending: [] });
    expect(await installAllowedNow()).toBe(true);
  });

  it("a failed pip run shows the restart banner too", () => {
    noteInstallError({ restart_required: true }, "faces");
    expect(restartBanner()).toContain("Restart Best Photo Picker");
    expect(/** @type {any} */ (window).installRestartPending).toEqual(["faces"]);
    noteInstallError({}, "faces");
  });
});

describe("restart banner", () => {
  afterEach(() => {
    delete (/** @type {any} */ (window)._isTourActive);
  });

  it("shows even while the product tour runs", () => {
    /** @type {any} */ (window)._isTourActive = () => true;
    showInstallDone("X", "inpaint");
    expect(restartBanner()).toContain("Restart Best Photo Picker");
  });

  it("a 409 after a reload with no known packages still marks the restart", () => {
    /** @type {any} */ (window).installRestartPending = [];
    toastInstallError(
      "install face recognition",
      Object.assign(new Error("HTTP 409"), {
        body: { error: "Restart first", context: { reason: "restart_pending" } },
      })
    );
    expect(/** @type {any} */ (window).installRestartPending).toEqual(["pending"]);
  });
});

describe("an install whose progress connection drops", () => {
  it("re-checks the server and shows a restart it needs", async () => {
    /** @type {any} */
    let source = null;
    vi.stubGlobal(
      "EventSource",
      vi.fn(function FakeEventSource() {
        source = this;
        this.close = vi.fn();
      })
    );
    let restartPending = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (/** @type {string} */ url) => {
        const body = url.includes("/install/state")
          ? { restart_pending: restartPending ? ["numpy"] : [] }
          : url.endsWith("/info")
            ? { packages: ["onnxruntime"], host: "pypi.org" }
            : { status: "started" };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      })
    );
    const done = installPackage(document.createElement("button"), "onnxruntime", "ONNX Runtime");
    await vi.waitFor(() => expect(source).not.toBeNull());
    source.onmessage({ data: JSON.stringify({ type: "start" }) });
    restartPending = true; // pip finished as the tab lost it
    source.onerror();
    await done;
    await vi.waitFor(() => expect(restartBanner()).toContain("Restart Best Photo Picker"));
    expect(toasts().join("\n")).toContain("the install may not have finished");
  });
});

describe("installs stopped before they finished", () => {
  it("are reported with the repair command, as text", async () => {
    const command = "/venv/bin/python -m pip install 'setuptools<81' '<b>x</b>'";
    stubState({ restart_pending: [], interrupted: [command] });
    await refreshInstallState();
    const banner = document.getElementById("nudge-install_interrupted");
    expect(banner?.textContent).toContain("stopped before it finished");
    expect(banner?.textContent).toContain(command);
    expect(banner?.querySelector("b")).toBeNull();
  });

  it("keeps the text current as they're repaired, then goes away", () => {
    showInterruptedInstalls(["pip install faces", "pip install lama"]);
    const banner = () => document.getElementById("nudge-install_interrupted")?.textContent || "";
    expect(banner()).toContain("pip install faces");
    showInterruptedInstalls(["pip install lama"]); // faces repaired
    expect(banner()).not.toContain("pip install faces");
    expect(banner()).toContain("pip install lama");
    showInterruptedInstalls([]);
    expect(document.getElementById("nudge-install_interrupted")).toBeNull();
  });

  it("still says so when the command isn't known", () => {
    showInterruptedInstalls([""]);
    const text = document.getElementById("nudge-install_interrupted")?.textContent || "";
    expect(text).toContain("install it again from the app");
  });
});

describe("where a stopped install can be redone", () => {
  const banner = () => document.getElementById("nudge-install_interrupted")?.textContent || "";

  it("faces and AI removal: in the app, or the terminal", () => {
    showInterruptedInstalls(["pip install f"], ["faces"]);
    expect(banner()).toContain("install it again from the app, or run this in a terminal");
  });

  it("others: only the terminal (no button would do it)", () => {
    showInterruptedInstalls(["pip install onnxruntime"], ["onnxruntime"]);
    expect(banner()).not.toContain("from the app");
    expect(banner()).toContain("run this in a terminal: pip install onnxruntime");
  });

  it("the face prompt says the install didn't finish", () => {
    document.body.innerHTML += '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    /** @type {any} */ (window).faceInstallable = true;
    /** @type {any} */ (window).interruptedInstalls = ["faces"];
    void _promptFaceInstall();
    const text = document.getElementById("confirm-overlay")?.textContent || "";
    expect(text).toContain("The face recognition install didn't finish.");
    expect(text).toContain("Install faces");
    delete (/** @type {any} */ (window).interruptedInstalls);
  });
});

describe("an install whose progress never starts", () => {
  it("says it didn't start, not that it may be unfinished", async () => {
    /** @type {any} */
    let source = null;
    vi.stubGlobal(
      "EventSource",
      vi.fn(function FakeEventSource() {
        source = this;
        this.close = vi.fn();
      })
    );
    stubState({ restart_pending: [] });
    const done = followInstall("faces");
    source.onerror(); // e.g. the GET was refused: claim expired
    await expect(done).rejects.toThrow("didn't start");
  });
});

describe("a status answer that predates the install's end", () => {
  it("doesn't take the restart banner down again", async () => {
    /** @type {(v: Response) => void} */
    let answer = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => (answer = resolve)))
    );
    const poll = refreshInstallState(); // asked before pip's "done"
    showInstallDone("Face recognition", "faces"); // the install ends
    answer(
      new Response(JSON.stringify({ restart_pending: [], face_installable: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    await poll;
    expect(restartBanner()).toContain("Restart Best Photo Picker");
    expect(/** @type {any} */ (window).faceInstallable).toBe(false);
  });
});
