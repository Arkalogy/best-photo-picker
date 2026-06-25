// @ts-check
import { expect, test } from "@playwright/test";

import { authToken, openApp } from "./_helpers.mjs";

test.describe("34 — Unknown API routes return 404 cleanly (not 500)", () => {
  test("nonsense path returns a JSON 404 instead of stack trace", async ({ page }) => {
    await openApp(page);

    const token = await authToken(page);
    const resp = await page.request.get(
      `http://localhost:5001/api/this-route-definitely-does-not-exist?_token=${token}`
    );

    // Flask default returns 404 for unknown routes; what we DON'T want
    // is 500 (server-side error) or a leaked stack trace.
    expect([404, 405]).toContain(resp.status());

    const body = await resp.text();
    // No Python traceback in the response (would mean exception leaked)
    expect(body).not.toMatch(/Traceback|line \d+ in/);
  });
});
