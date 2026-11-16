// @ts-check
import { expect, test } from "@playwright/test";

import { openApp } from "./_helpers.mjs";

/**
 * A settings switch must turn OFF as well as on. The change dispatcher
 * used to hand a checkbox handler the control's text value, which is
 * "on" whether it is ticked or not, so every switch read as enabled.
 */

/** @param {import("@playwright/test").Page} page */
async function openSettings(page) {
  await page.locator("#btn-settings-toolbar").click();
  await expect(page.locator("#settings-overlay")).toHaveClass(/visible/, { timeout: 5000 });
}

/** @param {import("@playwright/test").Page} page */
async function closeSettings(page) {
  await page.keyboard.press("Escape");
  await expect(page.locator("#settings-overlay")).not.toHaveClass(/visible/);
}

/**
 * Click the visible track of a switch (the checkbox itself is hidden).
 * @param {import("@playwright/test").Page} page
 * @param {string} id
 */
async function clickSwitch(page, id) {
  await page.locator(`label.setting-toggle:has(#${id}) .toggle-track`).click();
}

test.describe("55 — Settings switches turn off as well as on", () => {
  test("'Check for updates on startup' stays off after Settings is reopened", async ({ page }) => {
    await openApp(page);
    await openSettings(page);
    const toggle = page.locator("#check-updates-toggle");
    await expect(toggle).toBeChecked();

    await clickSwitch(page, "check-updates-toggle");
    await expect(toggle).not.toBeChecked();
    expect(await page.evaluate(() => localStorage.getItem("bpp_check_updates"))).toBe("false");

    await closeSettings(page);
    await openSettings(page);
    await expect(toggle).not.toBeChecked();

    await clickSwitch(page, "check-updates-toggle");
    await expect(toggle).toBeChecked();
    expect(await page.evaluate(() => localStorage.getItem("bpp_check_updates"))).toBe("true");
  });

  test("the LAN sharing switch sends a real true, then a real false", async ({ page }) => {
    // The toggle request is answered here, so the server's sharing state
    // never changes; the info reply is the real one with `enabled` patched
    // to follow what the switch last sent.
    /** @type {any[]} */
    const sent = [];
    let reportEnabled = false;
    await page.route("**/api/v1/share/info", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      json.enabled = reportEnabled;
      await route.fulfill({ response, json });
    });
    await page.route("**/api/v1/share/toggle", async (route) => {
      const body = route.request().postDataJSON();
      sent.push(body);
      reportEnabled = body.enabled === true;
      await route.fulfill({ json: { enabled: body.enabled } });
    });

    await openApp(page);
    await openSettings(page);
    await page.locator('.settings-tab[data-tab="share"]').click();
    const toggle = page.locator("#share-toggle");
    await expect(toggle).not.toBeChecked();

    await clickSwitch(page, "share-toggle");
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toEqual({ enabled: true });
    await expect(toggle).toBeChecked();

    await clickSwitch(page, "share-toggle");
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1]).toEqual({ enabled: false });
    await expect(toggle).not.toBeChecked();
  });

  test("'Follow shortcuts' switches on, and off again, and the server keeps each state", async ({
    page,
  }) => {
    const savedSetting = () =>
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/settings") && r.request().method() === "PUT"
      );
    const openAdvanced = async () => {
      await openSettings(page);
      await page.locator('.settings-tab[data-tab="advanced"]').click();
    };
    const toggle = page.locator("#follow-symlinks-toggle");

    await openApp(page);
    await openAdvanced();
    const startedOn = await toggle.isChecked();
    if (startedOn) {
      await Promise.all([savedSetting(), clickSwitch(page, "follow-symlinks-toggle")]);
    }
    await expect(toggle).not.toBeChecked();

    await Promise.all([savedSetting(), clickSwitch(page, "follow-symlinks-toggle")]);
    await expect(toggle).toBeChecked();
    await openApp(page); // reload: the state now comes from the server
    await openAdvanced();
    await expect(toggle).toBeChecked();

    await Promise.all([savedSetting(), clickSwitch(page, "follow-symlinks-toggle")]);
    await expect(toggle).not.toBeChecked();
    await openApp(page);
    await openAdvanced();
    await expect(toggle).not.toBeChecked();

    if (startedOn) {
      // Leave the library's setting as this test found it.
      await Promise.all([savedSetting(), clickSwitch(page, "follow-symlinks-toggle")]);
      await expect(toggle).toBeChecked();
    }
  });
});
