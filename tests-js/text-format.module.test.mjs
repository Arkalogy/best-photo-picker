// @ts-check
// Module-style tests — imports directly from the ES module under test.
// Unlike the non-module tests that use `new Function(...)` to load
// script-tag JS, these imports are instrumented by v8 and therefore
// COUNT TOWARDS COVERAGE.

import { describe, expect, test } from "vitest";

import { esc, escapeAttr, escVal, shortCount } from "../bpp/web/static/js/modules/text-format.mjs";

describe("esc (module)", () => {
  test("escapes angle brackets and ampersand", () => {
    expect(esc("<script>")).toBe("&lt;script&gt;");
    expect(esc("a & b")).toBe("a &amp; b");
  });
});

describe("escapeAttr (module)", () => {
  test("escapes every dangerous attribute character", () => {
    expect(escapeAttr('a"b')).toBe("a&quot;b");
    expect(escapeAttr("a'b")).toBe("a&#39;b");
    expect(escapeAttr("<x>&y")).toBe("&lt;x&gt;&amp;y");
  });

  test("ampersand is escaped before the replacements that introduce &", () => {
    // If & were replaced after < or >, we'd get &amp;lt; instead of &lt;.
    expect(escapeAttr("<")).toBe("&lt;");
  });
});

describe("escVal (module)", () => {
  test("a value that carries markup stays text, in an attribute or as content", () => {
    expect(escVal('x"><b>')).toBe("x&quot;&gt;&lt;b&gt;");
    expect(escVal("a'b&c")).toBe("a&#39;b&amp;c");
  });

  test("prints what a template would have printed", () => {
    expect(escVal(42)).toBe("42");
    expect(escVal(0)).toBe("0");
    expect(escVal(null)).toBe("null");
    expect(escVal(undefined)).toBe("undefined");
  });
});

describe("shortCount (module)", () => {
  test("plain integer under 1k", () => {
    expect(shortCount(0)).toBe("0");
    expect(shortCount(999)).toBe("999");
  });

  test("thousands with trimmed .0", () => {
    expect(shortCount(1000)).toBe("1k");
    expect(shortCount(1500)).toBe("1.5k");
    expect(shortCount(12345)).toBe("12.3k");
  });
});
