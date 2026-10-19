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
  feState,
  loadStateOf,
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  setLoadState,
} from "../bpp/web/static/js/modules/face-embedders-state.mjs";
import {
  ROW_LIFECYCLE,
  _buildMenuFromState,
  _deriveRowState,
} from "../bpp/web/static/js/modules/face-embedders-rowstate.mjs";
import { _statusCell, _statusFor } from "../bpp/web/static/js/modules/face-embedders-cells.mjs";
import {
  blockedBannerHTML,
  blockedItemLabel,
} from "../bpp/web/static/js/modules/analysis-preflight.mjs";
import {
  _acceptanceNotice,
  _acceptedToastText,
} from "../bpp/web/static/js/modules/face-embedders-acceptance.mjs";

beforeEach(() => {
  _resetFaceEmbedderStateForTests();
});

describe("pre-flight banner title (U7)", () => {
  const scrfd = { kind: "face_detector", name: "SCRFD", reason: "needs_license" };
  test("nothing blocked → no banner", () => {
    expect(blockedBannerHTML([])).toBe("");
  });
  test("never-accepted extras are 'Optional features available'", () => {
    const html = blockedBannerHTML([scrfd]);
    expect(html).toContain("Optional features available");
    expect(html).not.toContain("turned off");
  });
  const nudenet = { ...scrfd, name: "NudeNet", reason: "needs_reaccept" };
  test("only stale acceptances → features were turned off, singular for one", () => {
    const one = blockedBannerHTML([nudenet]);
    expect(one).toContain(">A feature was turned off<");
    expect(one).toContain("run without it for now");
    expect(one).not.toContain("available");
    expect(one).toContain("license terms were updated");
    const two = blockedBannerHTML([nudenet, { ...nudenet, name: "LaMa" }]);
    expect(two).toContain(">Some features were turned off<");
    expect(two).toContain("run without them for now");
  });
  test("a stale acceptance next to never-enabled extras says both, by count", () => {
    const one = blockedBannerHTML([scrfd, nudenet]);
    expect(one).toContain(">A feature was turned off; another is available<");
    expect(one).toContain("Its license terms were updated");
    const more = blockedBannerHTML([
      scrfd,
      { ...scrfd, name: "LaMa" },
      nudenet,
      { ...nudenet, name: "X" },
    ]);
    expect(more).toContain(">Some features were turned off; others are available<");
    expect(more).toContain("Some license terms were updated");
  });
});

describe("acceptance dialog notice for a stale acceptance (U8)", () => {
  const draft = { model_id: "scrfd" };
  test("first acceptance has no notice", () => {
    expect(_acceptanceNotice("accept", draft)).toBe("");
  });
  test("re-accept says the terms changed since you accepted", () => {
    _seedFaceEmbedderStateForTests({ requiresAckIds: ["scrfd"], reacceptIds: ["scrfd"] });
    const html = _acceptanceNotice("accept", draft);
    expect(html).toContain("fe-notice-banner");
    expect(html).toMatch(/updated .* you accepted/);
    expect(html).toMatch(/accept again/);
  });
  test("the server's load_state decides, like the row menu does", () => {
    // Acceptance list didn't load (reacceptIds empty) but the server says
    // needs_reaccept: the menu reads "Re-accept…", so must the dialog.
    _seedFaceEmbedderStateForTests({ requiresAckIds: ["scrfd"] });
    setLoadState("scrfd", "needs_reaccept");
    expect(_acceptanceNotice("accept", draft)).toMatch(/accept again/);
    setLoadState("scrfd", "needs_license");
    expect(_acceptanceNotice("accept", draft)).toBe("");
  });
  test("review mode keeps its accepted banner", () => {
    expect(_acceptanceNotice("review", draft)).toContain("fe-accepted-banner");
  });
});

describe("toast after accepting (U8)", () => {
  const entry = {
    id: "scrfd",
    display_name: "SCRFD",
    kind: "face_detector",
    is_catalog_entry: true,
  };
  test("weights not on disk → names the menu's own fetch action", () => {
    _seedFaceEmbedderStateForTests({
      acceptedIds: ["scrfd"],
      entries: { scrfd: { ...entry, catalog_on_disk: false } },
    });
    expect(_acceptedToastText("scrfd", "SCRFD")).toMatch(
      /^Acceptance recorded for SCRFD\. Next: Download.* from its ⋯ menu\.$/
    );
  });
  test("weights on disk → done", () => {
    _seedFaceEmbedderStateForTests({
      acceptedIds: ["scrfd"],
      entries: { scrfd: { ...entry, catalog_on_disk: true } },
    });
    expect(_acceptedToastText("scrfd", "SCRFD")).toBe("Acceptance recorded for SCRFD.");
  });
  test("reads the load state accept just set, not the entry's stale copy", () => {
    // confirmFaceEmbedderAcceptance flips the entry's load_state to "ok"; the
    // entry object still carries the pre-accept value until the picker
    // reloads. The menu (and so the hint) must follow the fresh value.
    const restricted = { ...entry, catalog_on_disk: false, load_state: "needs_license" };
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["scrfd"],
      acceptedIds: ["scrfd"],
      entries: { scrfd: restricted },
    });
    setLoadState("scrfd", "ok");
    expect(_acceptedToastText("scrfd", "SCRFD")).toMatch(/Next: Download/);
    // Not accepted yet (gate still refuses): Download is disabled → no hint.
    setLoadState("scrfd", "needs_license");
    expect(_acceptedToastText("scrfd", "SCRFD")).toBe("Acceptance recorded for SCRFD.");
  });
  test("install state unknown → the menu has no Download, so no hint", () => {
    _seedFaceEmbedderStateForTests({
      acceptedIds: ["sface_yunet"],
      entries: { sface_yunet: { id: "sface_yunet", display_name: "SFace", kind: "face_embedder" } },
    });
    feState.unknown.install = "HTTP 500";
    expect(_acceptedToastText("sface_yunet", "SFace")).toBe("Acceptance recorded for SFace.");
  });
  test("unknown entry → no guess", () => {
    expect(_acceptedToastText("nope", "Nope")).toBe("Acceptance recorded for Nope.");
  });
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
