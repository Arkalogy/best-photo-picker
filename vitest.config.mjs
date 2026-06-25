import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests-js/**/*.test.mjs"],
    globals: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
      include: ["bpp/web/static/js/**/*.{js,mjs}"],
      exclude: ["bpp/web/static/js/vendor/**"],
      // Non-module `.js` files are loaded via `new Function(...)` in the
      // test harness — v8 doesn't instrument those and they report 0%.
      // Real ES modules under bpp/web/static/js/modules/ DO count —
      // that's the migration path. Threshold stays off until a
      // meaningful fraction of source is module-loaded (target: 15%).
    },
  },
});
