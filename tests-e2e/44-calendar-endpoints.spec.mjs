// @ts-check
import { expect, test } from "@playwright/test";

import { api, openApp } from "./_helpers.mjs";

test.describe("44 — Calendar endpoints return shape we use in the UI", () => {
  test("/api/v1/calendar/months returns {months: [...]}", async ({ page }) => {
    await openApp(page);
    const data = await api(page, "/api/v1/calendar/months");
    expect(Array.isArray(data.months)).toBe(true);
  });

  test("/api/v1/calendar/year requires a valid year and returns months map", async ({ page }) => {
    await openApp(page);
    const monthsData = await api(page, "/api/v1/calendar/months");
    test.skip(
      !Array.isArray(monthsData.months) || monthsData.months.length === 0,
      "no months in library"
    );
    const someYear = monthsData.months[0].year || monthsData.months[0][0];
    test.skip(!someYear, "could not derive a year from months payload");

    const data = await api(page, `/api/v1/calendar/year?year=${someYear}`);
    expect(data.year).toBe(someYear);
    expect(typeof data.months).toBe("object");
  });
});
