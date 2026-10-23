// @ts-check
// The content filter, pet detector and object removal run although none
// is marked its kind's default: their loaders select them. The server
// marks what runs as ``primary_for_kind``; the picker must read it as
// running, not as a "Backup" that only steps in when a default fails.
import { beforeEach, describe, expect, test } from "vitest";

import {
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
} from "../bpp/web/static/js/modules/face-embedders-state.mjs";
import {
  _deriveRowState,
  ROW_LIFECYCLE,
} from "../bpp/web/static/js/modules/face-embedders-rowstate.mjs";
import { _renderRowForTests } from "../bpp/web/static/js/modules/modals-face-embedders.mjs";

const install = { status: "ready", files: [{ name: "nudenet.onnx", exists: true }] };

/** @param {boolean} primary */
function nudenet(primary) {
  return {
    id: "nudenet_320n",
    display_name: "NudeNet 320n",
    kind: "nudity_classifier",
    requires_explicit_ack: true,
    default_for_kind: false,
    primary_for_kind: primary,
    load_state: "ok",
  };
}

beforeEach(() => {
  _resetFaceEmbedderStateForTests();
  _seedFaceEmbedderStateForTests({
    requiresAckIds: ["nudenet_320n"],
    acceptedIds: ["nudenet_320n"],
    installState: { nudenet_320n: install },
  });
});

describe("sole-of-kind restricted model", () => {
  test("accepted and on disk → Running, not Backup", () => {
    const state = _deriveRowState(nudenet(true), install);
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.RUNNING);
    const html = _renderRowForTests(nudenet(true));
    expect(html).toMatch(/>Running</);
    expect(html).not.toMatch(/>Backup</);
    expect(html).not.toContain("auto-fallback");
  });

  test("not primary (another model of the kind is what runs) → Backup", () => {
    const state = _deriveRowState(nudenet(false), install);
    expect(state.lifecycle).toBe(ROW_LIFECYCLE.BACKUP_AVAILABLE);
  });

  test("primary but not yet accepted → needs its license, not Backup", () => {
    _seedFaceEmbedderStateForTests({
      requiresAckIds: ["nudenet_320n"],
      acceptedIds: [],
      installState: { nudenet_320n: install },
    });
    const entry = { ...nudenet(true), load_state: "needs_license" };
    expect(_deriveRowState(entry, install).lifecycle).toBe(ROW_LIFECYCLE.PICKED_NEEDS_LICENSE);
  });
});
