// @ts-check
// Module-style tests — imports directly from the ES module under test.
// Unlike the non-module tests that use `new Function(...)` to load
// script-tag JS, these imports are instrumented by v8 and therefore
// COUNT TOWARDS COVERAGE.

import { describe, expect, test } from "vitest";

import {
  esc,
  escapeAttr,
  escapeJsAttr,
  shortCount,
} from "../bpp/web/static/js/modules/text-format.mjs";

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

describe("escapeJsAttr (module)", () => {
  test("escapes backslashes and single quotes", () => {
    expect(escapeJsAttr("a\\b")).toBe("a\\\\b");
    expect(escapeJsAttr("a'b")).toBe("a\\'b");
  });

  test("still HTML-escapes angle brackets and double quotes", () => {
    expect(escapeJsAttr('"')).toBe("&quot;");
    expect(escapeJsAttr("<>")).toBe("\\x3c\\x3e");
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
