// @ts-check
import { describe, expect, test } from "vitest";

import { _lbComputeNewBbox } from "../bpp/web/static/js/modules/lightbox.mjs";

// _lbComputeNewBbox is the pure math used by the drag-to-fix face bbox UI.
// All coordinates are in percent of the image container; minPct defaults
// to 3. The clamp rules differ between move (preserve size, shift inside
// bounds) and resize (anchor the opposite edge, shrink as we hit a wall).

const START = { x: 20, y: 30, w: 40, h: 30 };

describe("_lbComputeNewBbox / move", () => {
  test("translates by both deltas when not against any wall", () => {
    const r = _lbComputeNewBbox(START, 5, -10, "move");
    expect(r).toEqual({ x: 25, y: 20, w: 40, h: 30 });
  });

  test("clamps to left edge without shrinking", () => {
    const r = _lbComputeNewBbox(START, -50, 0, "move");
    expect(r).toEqual({ x: 0, y: 30, w: 40, h: 30 });
  });

  test("clamps to right edge without shrinking", () => {
    const r = _lbComputeNewBbox(START, 99, 0, "move");
    expect(r).toEqual({ x: 60, y: 30, w: 40, h: 30 });
  });

  test("clamps to bottom edge", () => {
    const r = _lbComputeNewBbox(START, 0, 99, "move");
    expect(r).toEqual({ x: 20, y: 70, w: 40, h: 30 });
  });
});

describe("_lbComputeNewBbox / resize handles", () => {
  test("se corner grows width and height", () => {
    const r = _lbComputeNewBbox(START, 10, 5, "resize-se");
    expect(r).toEqual({ x: 20, y: 30, w: 50, h: 35 });
  });

  test("nw corner shrinks width+height and moves origin", () => {
    const r = _lbComputeNewBbox(START, 5, 10, "resize-nw");
    expect(r).toEqual({ x: 25, y: 40, w: 35, h: 20 });
  });

  test("e edge only changes width", () => {
    const r = _lbComputeNewBbox(START, 7, 9, "resize-e");
    expect(r).toEqual({ x: 20, y: 30, w: 47, h: 30 });
  });

  test("n edge moves top, shrinks height", () => {
    const r = _lbComputeNewBbox(START, 0, 5, "resize-n");
    expect(r).toEqual({ x: 20, y: 35, w: 40, h: 25 });
  });
});

describe("_lbComputeNewBbox / min size", () => {
  test("resize-e cannot shrink below minPct", () => {
    const r = _lbComputeNewBbox(START, -100, 0, "resize-e");
    expect(r.w).toBe(3);
    expect(r.x).toBe(START.x); // east edge: origin anchored
  });

  test("resize-w cannot shrink below minPct and anchors east edge", () => {
    const r = _lbComputeNewBbox(START, 100, 0, "resize-w");
    expect(r.w).toBe(3);
    // East edge of start is 20+40 = 60; minimum width keeps east anchored, so
    // new x = 60 - 3 = 57.
    expect(r.x).toBe(57);
  });

  test("custom minPct is honored", () => {
    const r = _lbComputeNewBbox(START, -100, 0, "resize-e", 10);
    expect(r.w).toBe(10);
  });
});

describe("_lbComputeNewBbox / image bounds", () => {
  test("resize-e past right edge shrinks instead of overflowing", () => {
    const r = _lbComputeNewBbox(START, 999, 0, "resize-e");
    expect(r.x + r.w).toBeLessThanOrEqual(100);
    expect(r.x).toBe(20);
    expect(r.w).toBe(80);
  });

  test("resize-s past bottom edge shrinks instead of overflowing", () => {
    const r = _lbComputeNewBbox(START, 0, 999, "resize-s");
    expect(r.y + r.h).toBeLessThanOrEqual(100);
    expect(r.h).toBe(70);
  });

  test("resize-nw past top-left shrinks both axes", () => {
    const r = _lbComputeNewBbox(START, -999, -999, "resize-nw");
    expect(r.x).toBe(0);
    expect(r.y).toBe(0);
    // East edge anchored at 60, south anchored at 60 → new w=60, h=60.
    expect(r.w).toBe(60);
    expect(r.h).toBe(60);
  });
});

describe("_lbComputeNewBbox / no-op", () => {
  test("zero delta on move returns identical bbox", () => {
    const r = _lbComputeNewBbox(START, 0, 0, "move");
    expect(r).toEqual(START);
  });
});
