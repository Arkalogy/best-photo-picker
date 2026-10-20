// @ts-check
/**
 * The pairing page tells the phone when the host can't be reached: a
 * failed or rejected status poll used to retry in silence under
 * "Waiting for approval…" for as long as the phone stayed on the page.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";

const html = readFileSync("bpp/web/templates/pair.html", "utf8");
const body = html.slice(html.indexOf("<body>") + 6, html.indexOf("<script"));
const script = html.slice(html.indexOf("(function(){"), html.lastIndexOf("</script>"));

beforeEach(() => {
  document.body.innerHTML = body;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("pairing page polls", () => {
  test("a network failure is shown, and polling continues", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      })
    );
    new Function(script)();
    await flush();
    await flush();
    expect(document.getElementById("pair-status")?.textContent).toMatch(/can't reach the host/i);
    expect(document.getElementById("pair-warn")?.textContent).toMatch(/retry/i);
  });

  test("a rejected poll (server error) is shown too", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("oops", { status: 500 }))
    );
    new Function(script)();
    await flush();
    await flush();
    await flush();
    expect(document.getElementById("pair-status")?.textContent).toMatch(/can't reach the host/i);
  });

  test("a refusal (4xx) is reported as a refusal, not an unreachable host", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 403 }))
    );
    new Function(script)();
    await flush();
    await flush();
    await flush();
    expect(document.getElementById("pair-status")?.textContent).toMatch(
      /refused this request \(HTTP 403\)/
    );
    expect(document.getElementById("pair-warn")?.textContent).toMatch(/share link/i);
  });

  test("a good poll after a failure clears the notice", async () => {
    vi.useFakeTimers();
    let n = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        n += 1;
        if (n === 1) throw new TypeError("Failed to fetch");
        return new Response(JSON.stringify({ state: "pending" }), { status: 200 });
      })
    );
    new Function(script)();
    await vi.advanceTimersByTimeAsync(10);
    expect(document.getElementById("pair-status")?.textContent).toMatch(/can't reach/i);
    await vi.advanceTimersByTimeAsync(3100);
    expect(document.getElementById("pair-status")?.textContent).toMatch(/waiting for approval/i);
    expect(document.getElementById("pair-warn")?.textContent).toBe("");
  });
});
