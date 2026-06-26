// Ambient global declarations for non-module runtime JS.
//
// Our runtime JS is script-tag loaded; TypeScript can't resolve
// cross-file references. This file hand-declares the globals that
// any //@ts-check-enabled source file needs to reference.
//
// Grow this list opt-in — when adding @ts-check to a new file, add
// the globals it actually uses here.

declare global {
  // From globals.js
  const APP_CONFIG: { name: string };
  const MONTHS_SHORT: readonly string[];
  const MONTHS_FULL: readonly string[];

  // Leaflet (loaded from CDN)
  const L: any;

  // P8: window extensions. The action registry exposes itself on window
  // for the click dispatcher; tests reach for arbitrary window.X bindings
  // to simulate legacy fallback paths.
  interface Window {
    __bppActionRegistry?: Map<string, Function>;
    [key: string]: any;
  }

  // Common browser helpers are already in lib.dom, no need to redeclare.
}

export {};
