// A full-size photo that fails to load (e.g. a RAW file with RAW support
// missing) is explained once, as an error toast with the server's reason
// and recovery — previews load via <img>, which can't show the error body.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const toastError = vi.fn();
vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({ toastError }));

/**
 * @param {number} status
 * @param {any} body
 */
function respond(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("full-size photo load failure → explained once", () => {
  /** @type {any} */
  let api;
  /** @type {ReturnType<typeof vi.fn>} */
  let fetchMock;

  beforeEach(async () => {
    vi.resetModules();
    toastError.mockClear();
    vi.stubGlobal("location", { reload: vi.fn(), origin: "http://localhost" });
    fetchMock = vi.fn(async (url) =>
      /^\/(photo\/|api\/v1\/(faces|pets)\/crop\/)/.test(String(url))
        ? respond(500, {
            error: "RAW support isn't available in this install",
            code: "internal",
            context: { recovery: "reinstall the desktop app" },
          })
        : respond(200, { ok: true })
    );
    vi.stubGlobal("fetch", fetchMock);
    api = await import("../bpp/web/static/js/modules/api-client.mjs");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("toasts the server's reason and recovery", async () => {
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x");
    expect(toastError).toHaveBeenCalledTimes(1);
    const [action, err, recovery] = toastError.mock.calls[0];
    expect(action).toBe("show this photo");
    expect(err.message).toBe("RAW support isn't available in this install");
    expect(recovery).toBe("reinstall the desktop app");
  });

  test("explains each photo once", async () => {
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x");
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x&t=2");
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  test("stays quiet when the photo loads on the second try, without downloading it", async () => {
    const cancel = vi.fn(async () => {});
    fetchMock.mockImplementation(async () => ({ ok: true, status: 200, body: { cancel } }));
    await api.explainPhotoFailure("http://localhost/photo/ok?_token=x");
    expect(toastError).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalled();
  });

  test("a blip doesn't hide a later real failure of the same photo", async () => {
    const cancel = vi.fn(async () => {});
    fetchMock.mockImplementationOnce(async () => ({ ok: true, status: 200, body: { cancel } }));
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x");
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x");
    expect(toastError).toHaveBeenCalledTimes(1);
  });

  test("leaves a lost session to the reload handler", async () => {
    fetchMock.mockImplementation(async () => respond(403, { error: "forbidden" }));
    await api.explainPhotoFailure("http://localhost/photo/abc?_token=x");
    expect(toastError).not.toHaveBeenCalled();
  });

  test("a failed full-size <img> triggers the explanation; thumbnails don't", async () => {
    // Called directly: every fresh import in this file adds its own
    // document listener, so dispatching a real event would hit them all.
    for (const url of [
      "http://localhost/thumb/t1?_token=x",
      "http://localhost/photo/p1?_token=x",
    ]) {
      const el = document.createElement("img");
      el.src = url;
      api._onMediaError(/** @type {any} */ ({ target: el }));
    }
    await vi.waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    const photoFetches = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/photo/"));
    expect(photoFetches.map((c) => String(c[0]))).toEqual(["/photo/p1?_token=x"]);
    expect(fetchMock.mock.calls.some((c) => String(c[0]).startsWith("/thumb/"))).toBe(false);
  });

  test("failing crops are explained once per session, not once per card", async () => {
    await api.explainPhotoFailure("http://localhost/api/v1/faces/crop/abc/0?_token=x");
    await api.explainPhotoFailure("http://localhost/api/v1/faces/crop/abc/1?_token=x");
    await api.explainPhotoFailure("http://localhost/api/v1/faces/crop/other/0?_token=x");
    await api.explainPhotoFailure("http://localhost/api/v1/pets/crop/abc/0?_token=x");
    await api.explainPhotoFailure("http://localhost/api/v1/pets/crop/third/2?_token=x");
    expect(toastError).toHaveBeenCalledTimes(2);
    expect(toastError.mock.calls[0][0]).toBe("show this face");
    expect(toastError.mock.calls[1][0]).toBe("show this pet");
    expect(toastError.mock.calls[0][2]).toBe("reinstall the desktop app");
  });
});
