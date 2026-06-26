// @ts-check
import { expect, test } from "@playwright/test";

test.describe("35 — Unauthenticated API calls are rejected (auth gate works)", () => {
  test("GET /api/state with no token returns 401/403", async ({ request }) => {
    const resp = await request.get("http://localhost:5001/api/state");
    // App-wide auth middleware rejects requests with missing tokens.
    // Either 401 (Unauthorized) or 403 (Forbidden) is acceptable —
    // pin both to avoid coupling to the chosen status.
    expect([401, 403]).toContain(resp.status());
  });

  test("GET /api/photos with bogus token returns 401/403", async ({ request }) => {
    const resp = await request.get("http://localhost:5001/api/photos?_token=this-is-not-the-token");
    expect([401, 403]).toContain(resp.status());
  });

  test("GET / (HTML) succeeds without token (the page bootstraps it)", async ({ request }) => {
    const resp = await request.get("http://localhost:5001/");
    expect(resp.status()).toBe(200);
    const body = await resp.text();
    // Auth token meta tag is rendered server-side
    expect(body).toMatch(/<meta\s+name="auth-token"/);
  });
});
