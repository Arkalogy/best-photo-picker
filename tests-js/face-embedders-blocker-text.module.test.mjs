// @ts-check
// A license blocker is worded once: the menu sub-line and the
// "Couldn't switch" toast come from the same helper, so they can't
// disagree about why a model can't run.
import { describe, expect, test } from "vitest";

import { licenseBlocker } from "../bpp/web/static/js/modules/face-embedders-license.mjs";

const cases = [
  [{ unavailable: true }, "No longer available", "it has been withdrawn — pick another model."],
  [
    { needsCommercialRights: true },
    "Confirm commercial-use rights first",
    "your commercial-use rights for it aren't confirmed — confirm them in its ⋯ menu.",
  ],
  [
    { needsReaccept: true },
    "Re-accept the license first",
    "its license terms changed — re-accept them in its ⋯ menu.",
  ],
  [{}, "Accept the license first", "its license isn't accepted — accept it in its ⋯ menu."],
];

describe("licenseBlocker", () => {
  test.each(cases)("%o → one short line and one reason-with-fix", (state, short, sentence) => {
    const b = licenseBlocker(/** @type {any} */ (state));
    expect(b.short).toBe(short);
    expect(`${b.reason} — ${b.fix}.`).toBe(sentence);
  });

  test("withdrawn wins over every other blocker", () => {
    const b = licenseBlocker({
      unavailable: true,
      needsCommercialRights: true,
      needsReaccept: true,
    });
    expect(b.short).toBe("No longer available");
  });
});
