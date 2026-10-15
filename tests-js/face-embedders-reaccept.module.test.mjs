// @ts-check
// Stale-acceptance ("re-accept needed") handling in the Settings → Models
// picker and the analyze pre-flight dialog. A disclaimer-version bump
// invalidates earlier acceptances server-side; the UI must not keep
// showing those models as accepted while the load gate refuses them.
import { beforeEach, describe, expect, test } from "vitest";

import {
  _collectAcceptedIds,
  _collectReacceptIds,
  damagedLogNoticeHTML,
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
} from "../bpp/web/static/js/modules/face-embedders-state.mjs";
import {
  ROW_LIFECYCLE,
  _buildMenuFromState,
  _deriveRowState,
} from "../bpp/web/static/js/modules/face-embedders-rowstate.mjs";
import { _statusCell, _statusFor } from "../bpp/web/static/js/modules/face-embedders-cells.mjs";
import { blockedItemLabel } from "../bpp/web/static/js/modules/analysis-preflight.mjs";

beforeEach(() => {
  _resetFaceEmbedderStateForTests();
});

describe("_collectAcceptedIds / _collectReacceptIds", () => {
  const payload = {
    acceptances: [
      { model_id: "fresh", event: "accept", current: true, needs_reaccept: false },
      { model_id: "stale", event: "accept", current: false, needs_reaccept: true },
      { model_id: "withdrawn", event: "accept", current: true },
      { model_id: "withdrawn", event: "revoke", current: false, needs_reaccept: false },
      { model_id: "legacy" }, // older server: no event / current flags
      // stale then re-accepted → latest row wins
      { model_id: "renewed", event: "accept", current: false, needs_reaccept: true },
      { model_id: "renewed", event: "accept", current: true, needs_reaccept: false },
    ],
  };

  test("only CURRENT acceptances count as accepted", () => {
    expect([..._collectAcceptedIds(payload)].sort()).toEqual(["fresh", "legacy", "renewed"]);
  });

  test("stale acceptances are collected as re-accept needed", () => {
    expect([..._collectReacceptIds(payload)]).toEqual(["stale"]);
  });

  test("refused but not an older text (a damaged log line after it): neither accepted nor re-accept", () => {
    const damaged = {
      acceptances: [{ model_id: "m", event: "accept", current: false, needs_reaccept: false }],
    };
    expect(_collectAcceptedIds(damaged).has("m")).toBe(false);
    expect(_collectReacceptIds(damaged).has("m")).toBe(false);
  });

  test("missing / malformed payload → empty sets", () => {
    expect(_collectAcceptedIds(null).size).toBe(0);
    expect(_collectReacceptIds(null).size).toBe(0);
    expect(_collectReacceptIds({}).size).toBe(0);
  });
});

describe("row state for a stale acceptance", () => {
  const nudenet = {
    id: "nudenet_320n",
    display_name: "NudeNet 320n",
    kind: "nudity_classifier",
    requires_explicit_ack: true,
    default_for_kind: false,
    is_catalog_entry: true,
    catalog_on_disk: true,
    expected_download_size_bytes: 12_150_158,
  };

  test("stale → license-blocked + needsReaccept, not accepted", () => {
    _seedFaceEmbedderStateForTests({ reacceptIds: [nudenet.id] });
    const state = _deriveRowState(nudenet, undefined);
    expect(state.accepted).toBe(false);
    expect(state.licenseBlocked).toBe(true);
    expect(state.needsReaccept).toBe(true);
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.NEEDS_LICENSE);
  });

  test("status cell says 'Re-accept needed', not 'Needs license'", () => {
    _seedFaceEmbedderStateForTests({ reacceptIds: [nudenet.id] });
    const state = _deriveRowState(nudenet, undefined);
    expect(_statusFor(state).main).toBe("Re-accept needed");
    const html = _statusCell(state);
    expect(html).toContain("Re-accept needed");
    expect(html).toContain("license terms updated");
  });

  test("menu offers the re-accept action and blocks download until then", () => {
    _seedFaceEmbedderStateForTests({ reacceptIds: [nudenet.id] });
    const state = _deriveRowState(nudenet, undefined);
    const items = _buildMenuFromState(nudenet, state);
    const license = items.find((i) => i.action === "openFaceEmbedderAcceptance");
    expect(license?.label).toBe("Re-accept the model’s license (terms updated)");
    expect(license?.enabled).toBe(true);
    const dl = items.find((i) => i.action === "ensureCatalogWeights");
    expect(dl?.enabled).toBe(false);
    expect(dl?.sub).toBe("Re-accept the license first");
  });

  test("current acceptance → normal accepted state, no re-accept badge", () => {
    _seedFaceEmbedderStateForTests({ acceptedIds: [nudenet.id] });
    const state = _deriveRowState(nudenet, undefined);
    expect(state.needsReaccept).toBe(false);
    expect(_statusFor(state).main).not.toBe("Re-accept needed");
  });

  test("never accepted → generic 'Needs license', not re-accept", () => {
    const state = _deriveRowState(nudenet, undefined);
    expect(state.needsReaccept).toBe(false);
    expect(_statusFor(state).main).toBe("Needs license");
    const license = _buildMenuFromState(nudenet, state).find(
      (i) => i.action === "openFaceEmbedderAcceptance"
    );
    expect(license?.label).toBe("Review & accept the model’s license");
  });
});

describe("blockedItemLabel (analyze pre-flight)", () => {
  test("research-only, non-default extra reads as optional + research-only", () => {
    expect(
      blockedItemLabel({
        kind: "face_detector",
        reason: "needs_license",
        research_only: true,
        default_for_kind: false,
      })
    ).toBe("Extra face detection (optional, research-only)");
  });

  test("default capability keeps the plain capability name", () => {
    expect(
      blockedItemLabel({ kind: "pet_detector", reason: "needs_license", research_only: false })
    ).toBe("Pet detection");
  });

  test("stale acceptance says re-accept", () => {
    expect(blockedItemLabel({ kind: "nudity_classifier", reason: "needs_reaccept" })).toBe(
      "Content filter — license terms updated, re-accept to turn back on"
    );
  });

  test("unknown kind falls back to the entry name", () => {
    expect(blockedItemLabel({ kind: "mystery", name: "Thing" })).toBe("Thing");
  });
});

describe("damaged license record notice", () => {
  test("names how many models need accepting again", () => {
    expect(damagedLogNoticeHTML({ withdrawn_by_damage: ["a"] })).toContain("1 model you accepted");
    expect(damagedLogNoticeHTML({ withdrawn_by_damage: ["a", "b"] })).toContain(
      "2 models you accepted before it now need to"
    );
  });

  test("nothing when the record is intact or the payload is odd", () => {
    expect(damagedLogNoticeHTML({ withdrawn_by_damage: [] })).toBe("");
    expect(damagedLogNoticeHTML(null)).toBe("");
    expect(damagedLogNoticeHTML({ withdrawn_by_damage: "x" })).toBe("");
  });
});
