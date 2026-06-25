// @ts-check
/**
 * Light/dark theme management.
 *
 * Theme is applied as a `data-theme` attribute on `<html>` and
 * persisted in localStorage under "bpp-theme". When running inside
 * Tauri, the native window chrome (titlebar / traffic lights) is
 * synced via the `set_app_theme` invoke command so dark mode looks
 * consistent edge-to-edge.
 *
 * Bridged onto window via index.html's module bootstrap so the
 * existing classic callers (initTheme from app.js, setTheme from
 * settings) keep working.
 */

const THEME_KEY = "bpp-theme";

/**
 * Apply a theme to the document, persist it, and update any
 * `.theme-btn` toggles in the UI to reflect the active state.
 *
 * @param {"dark" | "light" | "auto"} theme
 */
export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    btn.classList.toggle(
      "active",
      /** @type {HTMLElement} */ (btn).dataset.theme === theme,
    );
  });
  // Sync native window chrome when running inside Tauri.
  /** @type {any} */
  const tauri = /** @type {any} */ (window).__TAURI__;
  if (tauri?.core?.invoke) {
    const p = tauri.core.invoke("set_app_theme", { theme });
    if (p && typeof p.catch === "function") p.catch(() => {});
  }
}

/**
 * Persist + apply. Differs from applyTheme only in that the persist
 * happens before apply (kept for symmetry with prior behavior).
 *
 * @param {"dark" | "light" | "auto"} theme
 */
export function setTheme(theme) {
  localStorage.setItem(THEME_KEY, theme);
  applyTheme(theme);
}

/**
 * Read the persisted theme (default "dark") and apply it. Called
 * once on app boot from initApp().
 */
export function initTheme() {
  const saved = /** @type {"dark" | "light" | "auto"} */ (
    localStorage.getItem(THEME_KEY) || "dark"
  );
  applyTheme(saved);
}
