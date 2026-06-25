// @ts-check
import { describe, expect, test } from "vitest";

import {
  humanizeActivity,
  humanizeActivityList,
} from "../bpp/web/static/js/modules/activity-humanize.mjs";

/** Build a raw ring-buffer entry. @param {string} msg @param {string} [level] */
const e = (msg, level = "INFO") => ({ ts: 1000, level, msg });

describe("humanizeActivity — friendly milestones", () => {
  test("import starting", () => {
    expect(humanizeActivity(e("Import starting: src=/x, extensions=..."))?.text).toBe(
      "Importing photos…"
    );
  });

  test("import complete with comma-formatted count + pluralization", () => {
    expect(humanizeActivity(e("Import complete: 1200 imported, 3 skipped, 0 errors"))?.text).toBe(
      "Imported 1,200 photos"
    );
    expect(humanizeActivity(e("Import complete: 1 imported, 0 skipped, 0 errors"))?.text).toBe(
      "Imported 1 photo"
    );
  });

  test("analysis started", () => {
    expect(humanizeActivity(e("Analysis started"))?.text).toBe("Analyzing your photos…");
  });

  test("scoring done -> 'Analyzed N photos' (count from results, not images)", () => {
    expect(
      humanizeActivity(e("Scoring subprocess done (pid=89168): 3838 results from 3850 images"))
        ?.text
    ).toBe("Analyzed 3,838 photos");
  });

  test("new sensitive flags -> friendly review pointer", () => {
    expect(
      humanizeActivity(e("Flagged 12 new photo(s) as possibly sensitive (43 total)"))?.text
    ).toBe("Flagged 12 photos as possibly sensitive — review in the Sensitive album");
  });

  test("single sensitive flag -> singular phrasing", () => {
    expect(
      humanizeActivity(e("Flagged 1 new photo(s) as possibly sensitive (1 total)"))?.text
    ).toBe("Flagged 1 photo as possibly sensitive — review in the Sensitive album");
  });

  test("selection -> 'Selected your N best photos'", () => {
    expect(
      humanizeActivity(e("Selection: 47/47 chosen from 3341 candidates (47 months covered)"))?.text
    ).toBe("Selected your 47 best photos");
  });

  test("near-duplicate clustering with hits", () => {
    expect(
      humanizeActivity(
        e(
          "Near-duplicate clustering: 3842 photos → 6 non-singleton clusters (12 photos have a near-duplicate; threshold=8 bits)"
        )
      )?.text
    ).toBe("Found 12 possible duplicate photos");
  });

  test("near-duplicate clustering with zero hits is hidden", () => {
    expect(
      humanizeActivity(
        e(
          "Near-duplicate clustering: 3842 photos → 0 non-singleton clusters (0 photos have a near-duplicate; threshold=8 bits)"
        )
      )
    ).toBeNull();
  });
});

describe("humanizeActivity — capability notes (gentle, kept as warnings)", () => {
  test("pet model failure -> gentle note at WARNING level", () => {
    const h = humanizeActivity(e("Pet model download failed, pet detection disabled", "WARNING"));
    expect(h?.text).toBe("Pet detection isn't available on this device");
    expect(h?.level).toBe("WARNING");
  });

  test("CLIP model download failure -> gentle note", () => {
    expect(humanizeActivity(e("CLIP model download failed: timeout", "ERROR"))?.text).toBe(
      "Some photo features are unavailable"
    );
  });

  test("downloading YOLO model -> friendly download text", () => {
    expect(
      humanizeActivity(e("Downloading YOLOv8n model to ~/.cache/bpp/models/yolo11n.onnx ..."))?.text
    ).toBe("Downloading pet-detection model…");
  });

  test("duplicate low-level YOLO failure is suppressed (preflight summary wins)", () => {
    expect(humanizeActivity(e("Failed to download YOLOv8n model", "WARNING"))).toBeNull();
  });
});

describe("humanizeActivity — technical plumbing is hidden", () => {
  const hidden = [
    "Phase 'scoring' done (pid=89168, exitcode=0, crashed=False)",
    "Phase 'scoring' started (pid=89168)",
    "Starting scoring subprocess for 3850 images",
    "Saved 12 pet detections to DB",
    "Wrote 3838 photos to DB",
    "CLIP Phase 3: 100/200 embeddings computed",
    "Semantic dedup pass 1 (time+clip): 3838 images -> 3838 clusters",
    "Dedup final: 3341 representatives",
    "Phase 5 backfill done in 1.2s",
    "Smart albums refreshed after startup backfill",
    "CLIP vocabulary initialised",
    "CLIP text initialised",
    "Starting SHA-256 backfill thread",
    "SHA-256 backfill thread finished: 0 photos updated",
    "Startup scan: all files present",
    "Starting server at http://127.0.0.1:5001",
    "Library: ~/Pictures/BestPhotoPickerDemo",
    "Recovery: no live ctx; leaving face_extraction_journal row abc in place",
    "Update check failed: Release info unavailable (repo not found or private).",
    "Face pipeline: starting cluster",
  ];
  for (const msg of hidden) {
    test(`hidden: ${msg.slice(0, 48)}`, () => {
      expect(humanizeActivity(e(msg))).toBeNull();
    });
  }
});

describe("humanizeActivity — level fallbacks", () => {
  test("unmatched INFO is hidden", () => {
    expect(humanizeActivity(e("some internal info nobody needs"))).toBeNull();
  });

  test("unmatched WARNING is hidden", () => {
    expect(humanizeActivity(e("some internal warning", "WARNING"))).toBeNull();
  });

  test("unmatched ERROR falls through to a generic note (never silent)", () => {
    const h = humanizeActivity(e("KeyError: 'foo' at module.py:42", "ERROR"));
    expect(h?.level).toBe("ERROR");
    expect(h?.text).toContain("Something went wrong");
  });

  test("strips the standard log prefix before matching", () => {
    expect(
      humanizeActivity(e("16:34:56 [INFO ] bpp.web.base_worker: Analysis started"))?.text
    ).toBe("Analyzing your photos…");
  });

  test("null / empty entries are safe", () => {
    expect(humanizeActivity(null)).toBeNull();
    expect(humanizeActivity(undefined)).toBeNull();
    expect(humanizeActivity({ ts: 1, level: "INFO" })).toBeNull();
  });
});

describe("humanizeActivityList", () => {
  test("drops hidden entries, preserves order of survivors", () => {
    const out = humanizeActivityList([
      e("Analysis started"),
      e("Phase 'scoring' done (pid=1, exitcode=0, crashed=False)"),
      e("Scoring subprocess done (pid=1): 10 results from 10 images"),
      e("Starting SHA-256 backfill thread"),
      e("Selection: 5/5 chosen from 10 candidates (1 months covered)"),
    ]);
    expect(out.map((x) => x.text)).toEqual([
      "Analyzing your photos…",
      "Analyzed 10 photos",
      "Selected your 5 best photos",
    ]);
  });
});
