// Regression guard: every /thumb/, /photo/, /video/ URL the frontend
// constructs must go through authedSrc() so the auth token rides along
// as ?_token=. Browsers can't send custom headers on <img>/<video>/CSS
// background-image, so query-param auth is the only thing standing
// between an unpaired LAN device and the raw photo bytes.
//
// Without this test, someone could revert one of the 13 module-file
// changes from H1 and the file would still parse, lint, and pass every
// other vitest — but the page would silently start leaking thumbs.

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const MODULES_DIR = "bpp/web/static/js/modules";

function listModuleFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...listModuleFiles(full));
    else if (full.endsWith(".mjs")) out.push(full);
  }
  return out;
}

// Patterns that count as a media URL being used as a request target.
// We deliberately catch the assignment / template-string / CSS-url
// flavours and skip querySelector substring matches (those are
// intentionally bare — the appended ?_token=... doesn't break them).
const MEDIA_URL_PATTERNS = [
  // Quoted string literal: "/thumb/" or "/photo/" or "/video/"
  /(["'])\/(?:thumb|photo|video)\/[^"']*\1/g,
  // Template-literal head: `/thumb/${...} or `/photo/${...}
  /`\/(?:thumb|photo|video)\/\$\{/g,
  // CSS background-image: url(/thumb/...) — bare slash, no quotes
  /url\(\/(?:thumb|photo|video)\//g,
];

// Lines that look like literal media URLs but are NOT request targets:
//   - querySelector(`img[src*="/thumb/${hash}"]`) — substring match, fine
//   - JSDoc comments / docblocks
//   - the explanatory comment in api-client.mjs
//
// Skip those by checking: is the match wrapped in authedSrc(...) OR
// inside a querySelector / src*= attribute selector?
function isWrapped(line, matchIdx) {
  const before = line.slice(0, matchIdx);
  // authedSrc(`/thumb/${hash}`) or authedSrc("/thumb/" + hash)
  if (/authedSrc\(\s*$/.test(before)) return true;
  // querySelector(`img[src*="/thumb/...`)
  if (/src\*=\s*["'`]?$/.test(before)) return true;
  // Comment line
  const trimmed = line.trim();
  if (trimmed.startsWith("//") || trimmed.startsWith("*")) return true;
  return false;
}

describe("Media URL auth regression", () => {
  it("every /thumb /photo /video URL goes through authedSrc()", () => {
    const files = listModuleFiles(MODULES_DIR);
    expect(files.length).toBeGreaterThan(20); // sanity: we found the modules dir

    const violations = [];
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      const lines = src.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        for (const pat of MEDIA_URL_PATTERNS) {
          pat.lastIndex = 0;
          let m;
          while ((m = pat.exec(line)) !== null) {
            if (!isWrapped(line, m.index)) {
              violations.push(`${file}:${i + 1}  ${line.trim()}`);
            }
          }
        }
      }
    }

    expect(
      violations,
      `Found ${violations.length} unwrapped media URL(s):\n${violations.join("\n")}`
    ).toEqual([]);
  });
});
