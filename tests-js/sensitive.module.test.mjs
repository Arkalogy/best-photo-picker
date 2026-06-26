// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const apiFetch = vi.fn(() => Promise.resolve({}));
const toast = vi.fn();
const toastError = vi.fn();
const appConfirm = vi.fn();
const saveSetting = vi.fn();

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch,
  authedSrc: (s) => s,
}));
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toast, toastError }));
vi.mock("../bpp/web/static/js/modules/dialogs.mjs", () => ({ appConfirm }));
vi.mock("../bpp/web/static/js/modules/settings-client.mjs", () => ({ saveSetting }));

const {
  sensitiveChipHTML,
  sensitiveCtxLabel,
  partitionSensitive,
  sensitiveReviewListHTML,
  readReviewSelections,
  lbToggleSensitive,
  getSensitiveMode,
  setSensitiveMode,
  _setSensitiveMode,
  _onSensitiveThresholdInput,
  _onSensitiveThresholdCommit,
} = await import("../bpp/web/static/js/modules/sensitive.mjs");

afterEach(() => {
  apiFetch.mockClear();
  toast.mockClear();
  toastError.mockClear();
  appConfirm.mockReset();
  saveSetting.mockClear();
});

describe("sensitiveChipHTML", () => {
  test("not flagged → empty string (no chip, no nag)", () => {
    expect(sensitiveChipHTML({ is_sensitive: false })).toBe("");
    expect(sensitiveChipHTML(null)).toBe("");
    expect(sensitiveChipHTML({})).toBe("");
  });

  test("model-flagged → chip with on-device wording", () => {
    const html = sensitiveChipHTML({ is_sensitive: true, sensitive_override: null });
    expect(html).toContain("May be sensitive");
    expect(html).toContain("content filter");
    expect(html).toContain("on this Mac");
    expect(html).toContain('data-action="lbToggleSensitive"');
  });

  test("user-flagged → chip says the user marked it", () => {
    const html = sensitiveChipHTML({ is_sensitive: true, sensitive_override: 1 });
    expect(html).toContain("You marked this photo");
  });
});

describe("sensitiveCtxLabel", () => {
  test("flagged photo offers the clear action", () => {
    expect(sensitiveCtxLabel({ is_sensitive: true })).toBe("Not sensitive");
  });
  test("clean photo offers the mark action", () => {
    expect(sensitiveCtxLabel({ is_sensitive: false })).toBe("Mark sensitive");
    expect(sensitiveCtxLabel(null)).toBe("Mark sensitive");
  });
});

describe("partitionSensitive", () => {
  test("splits flagged from clean, preserving order", () => {
    const a = { filepath: "a", is_sensitive: true };
    const b = { filepath: "b", is_sensitive: false };
    const c = { filepath: "c", is_sensitive: true };
    const { flagged, clean } = partitionSensitive([a, b, c]);
    expect(flagged.map((p) => p.filepath)).toEqual(["a", "c"]);
    expect(clean.map((p) => p.filepath)).toEqual(["b"]);
  });

  test("empty / missing input → both lists empty", () => {
    expect(partitionSensitive([])).toEqual({ flagged: [], clean: [] });
    expect(partitionSensitive(undefined)).toEqual({ flagged: [], clean: [] });
  });
});

describe("sensitiveReviewListHTML + readReviewSelections", () => {
  const flagged = [
    { filepath: "/x/a.jpg", filename: "a.jpg", thumb_hash: "h1" },
    { filepath: "/x/b.jpg", filename: "b.jpg", thumb_hash: "h2" },
  ];

  test("renders one checked row per flagged photo", () => {
    const html = sensitiveReviewListHTML(flagged, (h) => `/thumb/${h}`);
    const root = document.createElement("div");
    root.innerHTML = html;
    const boxes = root.querySelectorAll(".sensitive-review-keep");
    expect(boxes).toHaveLength(2);
    boxes.forEach((b) => expect(/** @type {HTMLInputElement} */ (b).checked).toBe(true));
    expect(root.querySelectorAll("img")[1].getAttribute("src")).toBe("/thumb/h2");
  });

  test("filenames are escaped", () => {
    const html = sensitiveReviewListHTML(
      [{ filepath: "/x", filename: '<img onerror="x">.jpg', thumb_hash: "h" }],
      (h) => h
    );
    expect(html).not.toContain("<img onerror");
  });

  test("readReviewSelections returns only kept filepaths", () => {
    const root = document.createElement("div");
    root.innerHTML = sensitiveReviewListHTML(flagged, (h) => h);
    const boxes = /** @type {NodeListOf<HTMLInputElement>} */ (
      root.querySelectorAll(".sensitive-review-keep")
    );
    boxes[0].checked = false; // user removes a.jpg from the export
    const keep = readReviewSelections(flagged, root);
    expect([...keep]).toEqual(["/x/b.jpg"]);
  });
});

describe("lbToggleSensitive", () => {
  /** @type {any} */
  const win = window;

  beforeEach(() => {
    win.lightboxIdx = 0;
    win.updateLightboxScores = vi.fn();
    win.loadAlbumList = vi.fn();
  });

  test("flagged photo → POSTs override 0 and clears the flag", async () => {
    const p = { filepath: "/x/a.jpg", is_sensitive: true, sensitive_override: null };
    win.currentGridItems = [p];
    apiFetch.mockResolvedValueOnce({ status: "ok", is_sensitive: false });

    await lbToggleSensitive();

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/photos/sensitive",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ filepath: "/x/a.jpg", override: 0 }),
      })
    );
    expect(p.is_sensitive).toBe(false);
    expect(p.sensitive_override).toBe(0);
    expect(win.updateLightboxScores).toHaveBeenCalledWith(p);
    expect(toast).toHaveBeenCalledWith("Marked as not sensitive");
  });

  test("clean photo → POSTs override 1 and sets the flag", async () => {
    const p = { filepath: "/x/b.jpg", is_sensitive: false };
    win.currentGridItems = [p];
    apiFetch.mockResolvedValueOnce({ status: "ok", is_sensitive: true });

    await lbToggleSensitive();

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/photos/sensitive",
      expect.objectContaining({
        body: JSON.stringify({ filepath: "/x/b.jpg", override: 1 }),
      })
    );
    expect(p.is_sensitive).toBe(true);
    expect(toast).toHaveBeenCalledWith("Marked as sensitive");
  });

  test("API failure → toastError with action + error, photo untouched", async () => {
    const p = { filepath: "/x/c.jpg", is_sensitive: true, sensitive_override: null };
    win.currentGridItems = [p];
    const err = new Error("HTTP 500");
    apiFetch.mockRejectedValueOnce(err);

    await lbToggleSensitive();

    expect(toastError).toHaveBeenCalledWith("update the sensitive flag", err);
    expect(p.is_sensitive).toBe(true);
    expect(p.sensitive_override).toBe(null);
  });

  test("no lightbox open → no-op", async () => {
    win.lightboxIdx = -1;
    await lbToggleSensitive();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe("reviewSensitiveBeforeExport", () => {
  /** @type {any} */
  const win = window;

  beforeEach(() => {
    document.body.innerHTML = '<div id="confirm-overlay"><div class="confirm-dialog"></div></div>';
    win.photos = [];
    win.currentGridItems = [];
  });

  test("no flagged photos in selection → silent pass-through", async () => {
    win.photos = [
      { filepath: "/x/a.jpg", is_sensitive: false },
      { filepath: "/x/b.jpg", is_sensitive: false },
    ];
    const { reviewSensitiveBeforeExport } =
      await import("../bpp/web/static/js/modules/sensitive.mjs");
    const res = await reviewSensitiveBeforeExport(["/x/a.jpg", "/x/b.jpg"]);
    expect(res).toEqual({ proceed: true, paths: ["/x/a.jpg", "/x/b.jpg"] });
    expect(appConfirm).not.toHaveBeenCalled();
  });

  test("flagged photo → dialog shown; cancel aborts the export", async () => {
    win.photos = [
      { filepath: "/x/a.jpg", filename: "a.jpg", thumb_hash: "h1", is_sensitive: true },
      { filepath: "/x/b.jpg", filename: "b.jpg", thumb_hash: "h2", is_sensitive: false },
    ];
    appConfirm.mockResolvedValueOnce(false);
    const { reviewSensitiveBeforeExport } =
      await import("../bpp/web/static/js/modules/sensitive.mjs");
    const res = await reviewSensitiveBeforeExport(["/x/a.jpg", "/x/b.jpg"]);
    expect(res.proceed).toBe(false);
    expect(appConfirm).toHaveBeenCalledWith(
      "1 of 2 photos may be sensitive",
      expect.stringContaining("on this Mac"),
      expect.objectContaining({ okLabel: "Export" })
    );
  });

  test("confirm with a photo unchecked → it is dropped from the paths", async () => {
    win.photos = [
      { filepath: "/x/a.jpg", filename: "a.jpg", thumb_hash: "h1", is_sensitive: true },
      { filepath: "/x/b.jpg", filename: "b.jpg", thumb_hash: "h2", is_sensitive: true },
      { filepath: "/x/c.jpg", filename: "c.jpg", thumb_hash: "h3", is_sensitive: false },
    ];
    // Simulate the dialog render + the user unchecking the first row,
    // then confirming. appConfirm receives bodyHTML; inject it into the
    // dialog like the real implementation does, uncheck, then resolve.
    appConfirm.mockImplementationOnce(async (_msg, _sub, opts) => {
      const dialog = document.querySelector(".confirm-dialog");
      if (dialog) dialog.innerHTML = opts.bodyHTML;
      const first = /** @type {HTMLInputElement | null} */ (
        document.querySelector('.sensitive-review-keep[data-idx="0"]')
      );
      if (first) first.checked = false;
      return true;
    });
    const { reviewSensitiveBeforeExport } =
      await import("../bpp/web/static/js/modules/sensitive.mjs");
    const res = await reviewSensitiveBeforeExport(["/x/a.jpg", "/x/b.jpg", "/x/c.jpg"]);
    expect(res.proceed).toBe(true);
    expect(res.paths).toEqual(["/x/b.jpg", "/x/c.jpg"]);
  });

  test("paths not in the photo pool are exported untouched", async () => {
    win.photos = [];
    const { reviewSensitiveBeforeExport } =
      await import("../bpp/web/static/js/modules/sensitive.mjs");
    const res = await reviewSensitiveBeforeExport(["/x/unknown.jpg"]);
    expect(res).toEqual({ proceed: true, paths: ["/x/unknown.jpg"] });
  });
});

describe("sensitive_in_picks 2-way control", () => {
  /** @type {any} */
  const win = window;

  function mountControl(active = "allow") {
    document.body.innerHTML =
      '<div class="theme-toggle" id="sensitive-toggle">' +
      `<button class="theme-btn${active === "allow" ? " active" : ""}" data-sens="allow" aria-checked="${active === "allow"}">Allow</button>` +
      `<button class="theme-btn${active === "exclude" ? " active" : ""}" data-sens="exclude" aria-checked="${active === "exclude"}">Exclude</button>` +
      "</div>";
  }

  afterEach(() => {
    document.body.innerHTML = "";
    delete win.scheduleRecompute;
  });

  test("getSensitiveMode reads the active button", () => {
    mountControl("exclude");
    expect(getSensitiveMode()).toBe("exclude");
  });

  test("getSensitiveMode defaults to 'allow' when the control is absent", () => {
    document.body.innerHTML = "";
    expect(getSensitiveMode()).toBe("allow");
  });

  test("getSensitiveMode defaults to 'allow' when nothing is active", () => {
    document.body.innerHTML =
      '<div id="sensitive-toggle"><button class="theme-btn" data-sens="allow"></button>' +
      '<button class="theme-btn" data-sens="exclude"></button></div>';
    expect(getSensitiveMode()).toBe("allow");
  });

  test("setSensitiveMode moves the active class + aria-checked", () => {
    mountControl("allow");
    setSensitiveMode("exclude");
    expect(getSensitiveMode()).toBe("exclude");
    const ex = document.querySelector('[data-sens="exclude"]');
    const al = document.querySelector('[data-sens="allow"]');
    expect(ex?.classList.contains("active")).toBe(true);
    expect(ex?.getAttribute("aria-checked")).toBe("true");
    expect(al?.classList.contains("active")).toBe(false);
    expect(al?.getAttribute("aria-checked")).toBe("false");
  });

  test("setSensitiveMode ignores unknown values (control unchanged)", () => {
    mountControl("allow");
    setSensitiveMode("garbage");
    expect(getSensitiveMode()).toBe("allow");
  });

  test("_setSensitiveMode sets the mode and triggers a recompute", () => {
    mountControl("allow");
    win.scheduleRecompute = vi.fn();
    _setSensitiveMode("exclude");
    expect(getSensitiveMode()).toBe("exclude");
    expect(win.scheduleRecompute).toHaveBeenCalledTimes(1);
  });

  test("_setSensitiveMode is a no-op when re-clicking the active mode", () => {
    mountControl("exclude");
    win.scheduleRecompute = vi.fn();
    _setSensitiveMode("exclude");
    expect(win.scheduleRecompute).not.toHaveBeenCalled();
  });
});

describe("flag-sensitivity threshold slider", () => {
  /** @type {any} */
  const win = window;

  afterEach(() => {
    document.body.innerHTML = "";
    delete win.loadAlbumList;
  });

  test("_onSensitiveThresholdInput updates the value label live (no persist)", () => {
    document.body.innerHTML = '<span id="sensitive-threshold-val">0.70</span>';
    _onSensitiveThresholdInput("0.65");
    expect(document.getElementById("sensitive-threshold-val").textContent).toBe("0.65");
    expect(saveSetting).not.toHaveBeenCalled();
  });

  test("_onSensitiveThresholdCommit persists + refreshes the smart album", async () => {
    win.loadAlbumList = vi.fn();
    await _onSensitiveThresholdCommit("0.65");
    expect(saveSetting).toHaveBeenCalledWith("sensitive_nudity_threshold", 0.65);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/albums/refresh-smart",
      expect.objectContaining({ method: "POST" })
    );
    expect(win.loadAlbumList).toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("0.65"));
  });

  test("_onSensitiveThresholdCommit ignores a non-numeric value", async () => {
    await _onSensitiveThresholdCommit("garbage");
    expect(saveSetting).not.toHaveBeenCalled();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  test("commit surfaces a toastError when the refresh fails", async () => {
    win.loadAlbumList = vi.fn();
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("HTTP 500"));
    await _onSensitiveThresholdCommit("0.8");
    expect(saveSetting).toHaveBeenCalledWith("sensitive_nudity_threshold", 0.8);
    expect(toastError).toHaveBeenCalledWith("update the sensitive threshold", expect.any(Error));
  });
});
