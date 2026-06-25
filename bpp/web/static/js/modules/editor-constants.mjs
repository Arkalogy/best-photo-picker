// @ts-check
/**
 * Editor presets, defaults, and lookup tables.
 *
 * Extracted from editor.mjs during the v0.1 cleanup. All purely
 * static data — no runtime state, no DOM, no API:
 *
 *   EDITOR_DEFAULTS  — initial values for every adjust slider
 *   ASPECT_RATIOS    — crop aspect-ratio presets
 *   STYLE_TONES, STYLE_COLORS, STYLE_GRID  — style picker matrix
 *   BUILT_IN_FILTERS — built-in filter presets (LUTs)
 *   ADJUST_SLIDERS   — slider config (key, label, min, max, etc.)
 *   AUTO_SECTIONS    — auto-enhance section names
 *
 * Re-exported from editor.mjs so existing references resolve via
 * the module-bridge unchanged.
 */

export const EDITOR_DEFAULTS = {
  brightness: 1.0,
  contrast: 1.0,
  saturation: 1.0,
  sharpness: 1.0,
  crop_x: null,
  crop_y: null,
  crop_w: null,
  crop_h: null,
  rotation: 0,
  flip_h: false,
  flip_v: false,
  warmth: 0.0,
  highlights: 0.0,
  shadows: 0.0,
  vignette: 0.0,
  grain: 0.0,
  fade: 0.0,
  redeye_points: null,
  filter_name: null,
  exposure: 0.0,
  brilliance: 0.0,
  black_point: 0.0,
  vibrance: 0.0,
  tint: 0.0,
  definition: 0.0,
  noise_reduction: 0.0,
  straighten: 0.0,
  perspective_v: 0.0,
  perspective_h: 0.0,
};

export const ASPECT_RATIOS = [
  { label: "Free", value: null },
  { label: "Original", value: "original" },
  { label: "1:1", value: 1 },
  { label: "4:3", value: 4 / 3 },
  { label: "3:2", value: 3 / 2 },
  { label: "16:9", value: 16 / 9 },
  { label: "4:5", value: 4 / 5 },
  { label: "5:7", value: 5 / 7 },
  { label: "3:5", value: 3 / 5 },
  { label: "3:4", value: 3 / 4 },
  { label: "2:3", value: 2 / 3 },
];

export const STYLE_TONES = ["Standard", "Vivid", "Dramatic", "Luminous", "B&W"];
export const STYLE_COLORS = [
  "Neutral",
  "Warm",
  "Cool",
  "Amber",
  "Rose",
  "Gold",
  "Teal",
  "Jade",
  "Slate",
];

/** @type {Record<string, Record<string, number>>} */
export const STYLE_GRID = {
  "Standard|Neutral": {},
  "Standard|Warm": { warmth: 0.3 },
  "Standard|Cool": { warmth: -0.3 },
  "Standard|Amber": { warmth: 0.5, tint: -0.1 },
  "Standard|Rose": { tint: 0.3, warmth: 0.1 },
  "Standard|Gold": { warmth: 0.55, tint: -0.3 },
  "Standard|Teal": { warmth: -0.45, tint: -0.35 },
  "Standard|Jade": { warmth: -0.2, tint: -0.6 },
  "Standard|Slate": { warmth: -0.4, saturation: 0.65 },
  "Vivid|Neutral": { contrast: 1.3, saturation: 1.4, vibrance: 0.3 },
  "Vivid|Warm": { contrast: 1.3, saturation: 1.4, vibrance: 0.3, warmth: 0.3 },
  "Vivid|Cool": { contrast: 1.3, saturation: 1.4, vibrance: 0.3, warmth: -0.3 },
  "Vivid|Amber": { contrast: 1.3, saturation: 1.3, vibrance: 0.3, warmth: 0.5, tint: -0.1 },
  "Vivid|Rose": { contrast: 1.3, saturation: 1.3, vibrance: 0.3, tint: 0.3, warmth: 0.1 },
  "Vivid|Gold": { contrast: 1.3, saturation: 1.3, vibrance: 0.3, warmth: 0.55, tint: -0.3 },
  "Vivid|Teal": { contrast: 1.3, saturation: 1.4, vibrance: 0.3, warmth: -0.45, tint: -0.35 },
  "Vivid|Jade": { contrast: 1.3, saturation: 1.5, vibrance: 0.3, warmth: -0.2, tint: -0.6 },
  "Vivid|Slate": { contrast: 1.3, saturation: 0.7, vibrance: 0.3, warmth: -0.4 },
  "Dramatic|Neutral": { contrast: 1.5, saturation: 0.8, brightness: 0.9, vignette: 0.3 },
  "Dramatic|Warm": {
    contrast: 1.5,
    saturation: 0.8,
    brightness: 0.9,
    vignette: 0.3,
    warmth: 0.3,
  },
  "Dramatic|Cool": {
    contrast: 1.5,
    saturation: 0.8,
    brightness: 0.9,
    vignette: 0.3,
    warmth: -0.3,
  },
  "Dramatic|Amber": {
    contrast: 1.5,
    saturation: 0.7,
    brightness: 0.9,
    vignette: 0.3,
    warmth: 0.5,
  },
  "Dramatic|Rose": {
    contrast: 1.5,
    saturation: 0.8,
    brightness: 0.9,
    vignette: 0.3,
    tint: 0.3,
  },
  "Dramatic|Gold": {
    contrast: 1.5,
    saturation: 0.7,
    brightness: 0.9,
    vignette: 0.3,
    warmth: 0.55,
    tint: -0.3,
  },
  "Dramatic|Teal": {
    contrast: 1.5,
    saturation: 0.8,
    brightness: 0.9,
    vignette: 0.3,
    warmth: -0.45,
    tint: -0.35,
  },
  "Dramatic|Jade": {
    contrast: 1.5,
    saturation: 0.9,
    brightness: 0.9,
    vignette: 0.3,
    warmth: -0.2,
    tint: -0.6,
  },
  "Dramatic|Slate": {
    contrast: 1.6,
    saturation: 0.5,
    brightness: 0.85,
    vignette: 0.35,
    warmth: -0.35,
  },
  "Luminous|Neutral": { brightness: 1.15, highlights: 0.2, shadows: 0.3, contrast: 0.9 },
  "Luminous|Warm": {
    brightness: 1.15,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    warmth: 0.3,
  },
  "Luminous|Cool": {
    brightness: 1.15,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    warmth: -0.3,
  },
  "Luminous|Amber": {
    brightness: 1.15,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    warmth: 0.5,
  },
  "Luminous|Rose": {
    brightness: 1.15,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    tint: 0.3,
  },
  "Luminous|Gold": {
    brightness: 1.15,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    warmth: 0.55,
    tint: -0.3,
  },
  "Luminous|Teal": {
    brightness: 1.1,
    highlights: 0.2,
    shadows: 0.3,
    contrast: 0.9,
    warmth: -0.45,
    tint: -0.35,
  },
  "Luminous|Jade": {
    brightness: 1.1,
    highlights: 0.15,
    shadows: 0.3,
    contrast: 0.9,
    warmth: -0.2,
    tint: -0.6,
  },
  "Luminous|Slate": {
    brightness: 1.05,
    highlights: 0.2,
    shadows: 0.25,
    contrast: 0.85,
    warmth: -0.4,
    saturation: 0.75,
  },
  "B&W|Neutral": { saturation: 0.0, contrast: 1.2 },
  "B&W|Warm": { saturation: 0.0, contrast: 1.2, warmth: 0.2 },
  "B&W|Cool": { saturation: 0.0, contrast: 1.2, warmth: -0.2 },
  "B&W|Amber": { saturation: 0.0, contrast: 1.1, warmth: 0.4 },
  "B&W|Rose": { saturation: 0.0, contrast: 1.2, tint: 0.2 },
  "B&W|Gold": { saturation: 0.0, contrast: 1.1, warmth: 0.5 },
  "B&W|Teal": { saturation: 0.0, contrast: 1.1, warmth: -0.4 },
  "B&W|Jade": { saturation: 0.0, contrast: 1.1, warmth: -0.2, tint: -0.3 },
  "B&W|Slate": { saturation: 0.0, contrast: 1.2, warmth: -0.35 },
};

export const BUILT_IN_FILTERS = [
  { name: "None", params: {} },
  { name: "Vivid", params: { contrast: 1.3, saturation: 1.4, brightness: 1.05 } },
  {
    name: "Dramatic",
    params: {
      contrast: 1.5,
      saturation: 0.8,
      brightness: 0.9,
      highlights: -0.3,
      shadows: 0.2,
      vignette: 0.4,
    },
  },
  { name: "B&W", params: { saturation: 0.0, contrast: 1.2 } },
  {
    name: "Noir",
    params: { saturation: 0.0, contrast: 1.6, brightness: 0.85, vignette: 0.5, fade: 0.1 },
  },
  { name: "Warm", params: { warmth: 0.5, saturation: 1.1 } },
  { name: "Cool", params: { warmth: -0.4, saturation: 0.9 } },
  { name: "Fade", params: { fade: 0.4, contrast: 0.9, saturation: 0.85 } },
  {
    name: "Vintage",
    params: {
      warmth: 0.3,
      fade: 0.3,
      saturation: 0.7,
      contrast: 1.1,
      vignette: 0.3,
      grain: 0.2,
    },
  },
  { name: "Chrome", params: { contrast: 1.4, saturation: 0.6, highlights: 0.2, shadows: -0.2 } },
  {
    name: "Film",
    params: { warmth: 0.15, fade: 0.2, grain: 0.15, contrast: 1.15, saturation: 0.85 },
  },
  {
    name: "Silvertone",
    params: { saturation: 0.1, contrast: 1.3, brightness: 1.05, fade: 0.15 },
  },
];

export const ADJUST_SLIDERS = [
  { key: "exposure", label: "Exposure", min: -2.0, max: 2.0, step: 0.01, default: 0.0, icon: "&#9788;", section: "Light" },
  { key: "brilliance", label: "Brilliance", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#10022;", section: "Light" },
  { key: "brightness", label: "Brightness", min: 0.2, max: 2.0, step: 0.01, default: 1.0, icon: "&#9728;", section: "Light" },
  { key: "contrast", label: "Contrast", min: 0.5, max: 2.0, step: 0.01, default: 1.0, icon: "&#9681;", section: "Light" },
  { key: "highlights", label: "Highlights", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9651;", section: "Light" },
  { key: "shadows", label: "Shadows", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9661;", section: "Light" },
  { key: "black_point", label: "Black Point", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9679;", section: "Light" },
  { key: "saturation", label: "Saturation", min: 0.0, max: 2.0, step: 0.01, default: 1.0, icon: "&#9752;", section: "Color" },
  { key: "vibrance", label: "Vibrance", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#10047;", section: "Color" },
  { key: "warmth", label: "Warmth", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9832;", section: "Color" },
  { key: "tint", label: "Tint", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9672;", section: "Color" },
  { key: "sharpness", label: "Sharpness", min: 0.0, max: 3.0, step: 0.01, default: 1.0, icon: "&#9670;", section: "Detail" },
  { key: "definition", label: "Definition", min: -1.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9677;", section: "Detail" },
  { key: "noise_reduction", label: "Noise Red.", min: 0.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#8226;", section: "Detail" },
  { key: "vignette", label: "Vignette", min: 0.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#9678;", section: "Effects" },
  { key: "fade", label: "Fade", min: 0.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#8943;", section: "Effects" },
  { key: "grain", label: "Grain", min: 0.0, max: 1.0, step: 0.01, default: 0.0, icon: "&#8901;", section: "Effects" },
];

export const AUTO_SECTIONS = ["Light", "Color"];

