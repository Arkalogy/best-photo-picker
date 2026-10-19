import { describe, expect, test } from "vitest";

import { licenseBadge } from "../bpp/web/static/js/modules/face-embedders-cells.mjs";
import { _renderRowForTests } from "../bpp/web/static/js/modules/modals-face-embedders.mjs";

describe("licenseBadge", () => {
  test("commercial-use restriction is Restricted", () => {
    const badge = licenseBadge({
      commercial_use_restriction_known: true,
      requires_explicit_ack: true,
    });
    expect(badge).toMatchObject({ text: "Restricted", cls: "fe-license-restricted" });
    expect(badge.tip).toMatch(/commercial use/i);
  });
  test("attribution-only click-through is Attribution", () => {
    const badge = licenseBadge({
      commercial_use_restriction_known: false,
      requires_explicit_ack: true,
    });
    expect(badge).toMatchObject({ text: "Attribution", cls: "fe-license-attribution" });
    expect(badge.tip).toMatch(/attribution/i);
  });
  test("no click-through is Permissive", () => {
    const badge = licenseBadge({
      commercial_use_restriction_known: false,
      requires_explicit_ack: false,
    });
    expect(badge).toMatchObject({ text: "Permissive", cls: "fe-license-permissive" });
    expect(badge.tip).toMatch(/no click-through/i);
  });
  test("missing fields default to Permissive", () => {
    expect(licenseBadge({}).text).toBe("Permissive");
  });
  test("every badge explains itself on hover (U9)", () => {
    for (const entry of [
      { commercial_use_restriction_known: true, requires_explicit_ack: true },
      { commercial_use_restriction_known: false, requires_explicit_ack: true },
      {},
    ]) {
      const { text, tip } = licenseBadge(entry);
      expect(tip.length, text).toBeGreaterThan(20);
      expect(tip, text).not.toContain('"');
      const html = _renderRowForTests({
        id: `badge_${text}`,
        display_name: "X",
        kind: "face_embedder",
        ...entry,
      });
      expect(html, text).toContain(`title="${tip}">${text}</span>`);
    }
  });
});
