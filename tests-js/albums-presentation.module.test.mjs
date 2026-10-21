// @ts-check
/**
 * The sidebar draws a smart album the way the server describes it: icon,
 * tooltip and position come with the album, so a plugin album type is
 * not stuck with the folder icon in the middle of the list.
 */
import { describe, expect, test } from "vitest";

import {
  smartAlbumIcon,
  smartAlbumTip,
} from "../bpp/web/static/js/modules/albums-render-helpers.mjs";
import { albumSortKey } from "../bpp/web/static/js/modules/albums-render.mjs";

const ICONS = { star: "<star/>", folder: "<folder/>", duplicate: "<dup/>" };

describe("smart album presentation", () => {
  test("uses the album's own icon, tooltip and order when the server sent them", () => {
    const album = {
      album_type: "smart_myplugin",
      icon: "star",
      tooltip: "Photos my plugin likes",
      sort_order: 3.5,
    };
    expect(smartAlbumIcon(album, ICONS)).toBe("<star/>");
    expect(smartAlbumTip(album)).toBe("Photos my plugin likes");
    expect(albumSortKey(album)).toBe(3.5);
  });

  test("a bare album has no icon or tooltip of its own (the server sends them)", () => {
    const album = { album_type: "smart_duplicates" };
    expect(smartAlbumIcon(album, ICONS)).toBe("<folder/>");
    expect(albumSortKey(album)).toBe(8);
    expect(smartAlbumTip({ album_type: "smart_score" })).toBe("");
  });

  test("a type the tables do not know gets the folder and the middle", () => {
    expect(smartAlbumIcon({ album_type: "smart_other" }, ICONS)).toBe("<folder/>");
    expect(albumSortKey({ album_type: "smart_other" })).toBe(5);
  });
});
