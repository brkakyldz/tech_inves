// Baked grain texture -- a small, fixed-size feTurbulence tile encoded once
// as a CSS data-URI background, NOT a live <filter> applied to DOM content.
// feTurbulence is one of the more expensive SVG filter primitives; baking it
// into a static raster-equivalent data URI and tiling it with
// `background-repeat` sidesteps the live-filter recompute cost entirely --
// the runtime cost is just decoding a small image once.
//
// Tile size: 200x200 (GRAIN_TILE_SIZE). Callers must tile via
// `background-repeat: repeat`, never stretch/cover -- stretching a 200px
// noise tile to fill a large surface would visibly smear the grain.

import type { CSSProperties } from "react";

/** Tile edge length in CSS px. Do not stretch -- tile with background-repeat. */
export const GRAIN_TILE_SIZE = 200;

// Recipe, per the researched range: fractalNoise, baseFrequency 0.65-0.9,
// numOctaves 2-3, stitchTiles="stitch" (seamless tiling across repeats),
// feColorMatrix collapsing to a single alpha channel at 0.03-0.08 (subtle).
const BASE_FREQUENCY = 0.8;
const NUM_OCTAVES = 2;
const GRAIN_ALPHA = 0.05;

function buildGrainSvg(): string {
  return (
    `<svg xmlns='http://www.w3.org/2000/svg' width='${GRAIN_TILE_SIZE}' height='${GRAIN_TILE_SIZE}'>` +
    `<filter id='g'>` +
    `<feTurbulence type='fractalNoise' baseFrequency='${BASE_FREQUENCY}' numOctaves='${NUM_OCTAVES}' stitchTiles='stitch'/>` +
    `<feColorMatrix type='matrix' values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 ${GRAIN_ALPHA} 0'/>` +
    `</filter>` +
    // Plain '#g' here -- this whole string is passed through
    // encodeURIComponent() below, which is what produces the '%23'. Writing
    // '%23g' here double-encodes to '%2523g' in the final data URI, which
    // decodes back to the literal (invalid) 'url(%23g)' rather than
    // 'url(#g)', so the filter reference silently fails to resolve. An
    // explicit non-black fill is a safety net for that failure mode: SVG's
    // default fill is black, so a broken filter reference must never leave
    // this rect to paint solid black over the hero.
    `<rect width='100%' height='100%' fill='transparent' filter='url(#g)'/>` +
    `</svg>`
  );
}

const GRAIN_SVG = buildGrainSvg();

/** Raw `data:image/svg+xml,...` URI. URL-encoded (not base64 -- smaller for SVG). */
export const GRAIN_DATA_URI = `data:image/svg+xml,${encodeURIComponent(GRAIN_SVG)}`;

/**
 * Ready-to-spread inline style: tiles the grain as a background image.
 * Spread onto an element (or an absolutely-positioned overlay) that sits
 * behind or over the content it should texture.
 */
export const GRAIN_BACKGROUND_STYLE: CSSProperties = {
  backgroundImage: `url("${GRAIN_DATA_URI}")`,
  backgroundRepeat: "repeat",
  backgroundSize: `${GRAIN_TILE_SIZE}px ${GRAIN_TILE_SIZE}px`,
};
