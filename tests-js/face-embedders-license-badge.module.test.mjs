import { describe, expect, test } from "vitest";

import { licenseBadge } from "../bpp/web/static/js/modules/face-embedders-cells.mjs";

describe("licenseBadge", () => {
  test("commercial-use restriction is Restricted", () => {
    expect(
      licenseBadge({ commercial_use_restriction_known: true, requires_explicit_ack: true })
    ).toEqual({ text: "Restricted", cls: "fe-license-restricted" });
  });
  test("attribution-only click-through is Attribution", () => {
    expect(
      licenseBadge({ commercial_use_restriction_known: false, requires_explicit_ack: true })
    ).toEqual({ text: "Attribution", cls: "fe-license-attribution" });
  });
  test("no click-through is Permissive", () => {
    expect(
      licenseBadge({ commercial_use_restriction_known: false, requires_explicit_ack: false })
    ).toEqual({ text: "Permissive", cls: "fe-license-permissive" });
  });
  test("missing fields default to Permissive", () => {
    expect(licenseBadge({}).text).toBe("Permissive");
  });
});
