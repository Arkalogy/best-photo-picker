// @ts-check
import { describe, expect, test } from "vitest";

import {
  MONTHS_FULL,
  MONTHS_SHORT,
  formatDate,
  formatDateStamp,
} from "../bpp/web/static/js/modules/date-format.mjs";

describe("MONTHS_SHORT / MONTHS_FULL", () => {
  test("12 entries each, in order", () => {
    expect(MONTHS_SHORT).toHaveLength(12);
    expect(MONTHS_FULL).toHaveLength(12);
    expect(MONTHS_SHORT[0]).toBe("Jan");
    expect(MONTHS_SHORT[11]).toBe("Dec");
    expect(MONTHS_FULL[0]).toBe("January");
    expect(MONTHS_FULL[11]).toBe("December");
  });
});

describe("formatDate", () => {
  test("returns '' for falsy input", () => {
    expect(formatDate("")).toBe("");
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
  });

  test("default style — Mon D, YYYY", () => {
    // Use mid-day timestamps to avoid timezone-induced date shifts
    // (date-only strings get parsed as UTC midnight by Date()).
    expect(formatDate("2024-06-15T12:00:00")).toBe("Jun 15, 2024");
    expect(formatDate("2024-01-15T12:00:00")).toBe("Jan 15, 2024");
    expect(formatDate("2024-12-15T12:00:00")).toBe("Dec 15, 2024");
  });

  test("time style — adds clock", () => {
    expect(formatDate("2024-06-15T15:45:00", "time")).toBe("Jun 15, 2024 · 3:45 PM");
  });

  test("time style on the hour — short form", () => {
    expect(formatDate("2024-06-15T09:00:00", "time")).toBe("Jun 15, 2024 · 9 AM");
  });

  test("time style — noon and midnight", () => {
    expect(formatDate("2024-06-15T12:00:00", "time")).toBe("Jun 15, 2024 · 12 PM");
    expect(formatDate("2024-06-15T00:00:00", "time")).toBe("Jun 15, 2024 · 12 AM");
  });

  test("unparseable input — returned as-is", () => {
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  test("relative style — recent", () => {
    const now = new Date();
    expect(formatDate(now.toISOString(), "relative")).toBe("just now");
    const fiveMinAgo = new Date(now.getTime() - 5 * 60 * 1000);
    expect(formatDate(fiveMinAgo.toISOString(), "relative")).toBe("5m ago");
  });

  test("relative style — months/years", () => {
    // Use a very old date that's definitely not "this year"
    expect(formatDate("2020-06-15T12:00:00", "relative")).toBe("Jun 15, 2020");
  });
});

describe("formatDateStamp", () => {
  test("equivalent to formatDate with no style", () => {
    expect(formatDateStamp("2024-06-15")).toBe(formatDate("2024-06-15"));
  });
});
