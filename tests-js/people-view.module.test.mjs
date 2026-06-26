// @ts-check
import { beforeEach, describe, expect, test } from "vitest";

import { personLabelHTML } from "../bpp/web/static/js/modules/people-view.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

beforeEach(() => {
  /** @type {any} */ (state).faceClusters = [{ cluster_id: 6, name: "", filepaths: [] }];
  /** @type {any} */ (state).albumList = [];
});

describe("personLabelHTML rename affordance", () => {
  test("name div dispatches startPersonRename with clusterId and no literal-string arg", () => {
    const html = personLabelHTML(6, 50);
    expect(html).toContain('data-action="startPersonRename"');
    expect(html).toContain('data-arg0="6"');
    // Regression: the card used to pass data-arg1="this.parentElement", which
    // the data-action dispatcher hands through as a literal STRING — so
    // startPersonRename got a string instead of the element and threw on
    // el.innerHTML, failing every grid rename. The arg must be gone.
    expect(html).not.toContain("this.parentElement");
    expect(html).not.toContain("data-arg1");
  });
});
