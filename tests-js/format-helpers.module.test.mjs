// @ts-check
// Module-style tests — counts towards v8 coverage.

import { describe, expect, test } from "vitest";

import { _formatBytes, formatVal, parseSSE } from "../bpp/web/static/js/modules/format-helpers.mjs";

describe("formatVal", () => {
  test("integer params round to nearest integer", () => {
    expect(formatVal("hash_distance_threshold", 12.7)).toBe("13");
    expect(formatVal("max_per_day", "5")).toBe("5");
    expect(formatVal("global_hash_distance_threshold", 0)).toBe("0");
  });

  test("non-integer params render to 2 decimals", () => {
    expect(formatVal("blur_weight", 0.5)).toBe("0.50");
    expect(formatVal("face_weight", "0.123")).toBe("0.12");
    expect(formatVal("anything_else", 1)).toBe("1.00");
  });
});

describe("_formatBytes", () => {
  test("zero byte literal", () => {
    expect(_formatBytes(0)).toBe("0 B");
  });

  test("bytes (< 1 KB) — no decimal", () => {
    expect(_formatBytes(512)).toBe("512 B");
    expect(_formatBytes(1023)).toBe("1023 B");
  });

  test("KB / MB / GB use one decimal place, base-1024", () => {
    expect(_formatBytes(1024)).toBe("1.0 KB");
    expect(_formatBytes(1536)).toBe("1.5 KB");
    expect(_formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(_formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(_formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
    expect(_formatBytes(2.5 * 1024 * 1024 * 1024)).toBe("2.5 GB");
  });
});

describe("parseSSE", () => {
  test("parses valid JSON", () => {
    expect(parseSSE('{"a":1}')).toEqual({ a: 1 });
    expect(parseSSE("[1,2,3]")).toEqual([1, 2, 3]);
    expect(parseSSE("null")).toBeNull();
  });

  test("returns null on garbled input rather than throwing", () => {
    expect(parseSSE("not json")).toBeNull();
    expect(parseSSE("")).toBeNull();
    expect(parseSSE("{")).toBeNull();
  });
});
