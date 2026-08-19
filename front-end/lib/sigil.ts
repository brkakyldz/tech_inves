// Deterministic parameter derivation for the TechInves score sigil.
//
// Pure functions only -- no React, no DOM, no Math.random(), no Date, no
// locale-dependent string ops (no .toUpperCase()/.toLowerCase() without an
// explicit locale -- Turkish's dotless-i case folding is exactly the kind of
// silent, locale-dependent divergence this file must never introduce).
//
// Same (ticker, score, subscores) input must always produce byte-identical
// output: the sigil is regenerated at every SSR/RSC render (no persisted
// image), so non-determinism here would show up as an inexplicable diff or a
// cache-busting card image on every request.

/**
 * FNV-1a 32-bit hash. Non-cryptographic, dependency-free, ~5 lines. Used
 * purely to pick stable-but-arbitrary per-ticker visual properties (hue,
 * base rotation) -- not for anything security-sensitive.
 */
export function hashTicker(ticker: string): number {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < ticker.length; i++) {
    hash ^= ticker.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193); // FNV prime
  }
  return hash >>> 0; // unsigned 32-bit
}

/** One category subscore rendered as a spoke. */
export interface SigilSpoke {
  /** Angle in degrees, 0 = up, clockwise. Rounded to 3 decimals. */
  angleDeg: number;
  /** 0-1 length fraction along the spoke's radius. 0 when absent. */
  lengthFrac: number;
  /**
   * true when the source subscore was null (no computable data for this
   * category). Absent spokes must NOT render as lengthFrac 0 alone -- that
   * reads as "measured, worst possible", a different and false claim from
   * "not measured". Renderers must draw a visibly distinct absent marker
   * (short tick / dashed stub) instead. Mirrors the convention in
   * components/companies/CategoryRadarChart.tsx:24-26, which omits
   * null-score categories from the radar entirely rather than plotting a 0.
   */
  absent: boolean;
}

export interface SigilParams {
  ticker: string;
  /** Stable per-company hue, degrees 0-360, from the ticker hash. */
  hue: number;
  /** Stable per-company base rotation, degrees 0-360, from the ticker hash. */
  baseRotationDeg: number;
  /** 0-1 fraction of the ring's circumference that is "filled" by the composite score. */
  ringFillFrac: number;
  /** true when the composite score itself was null -- ring renders as an absent track, not a 0-length fill. */
  ringAbsent: boolean;
  spokes: SigilSpoke[];
}

const PRECISION = 1000; // 3 decimal places

function round3(n: number): number {
  return Math.round(n * PRECISION) / PRECISION;
}

/**
 * The sigil's opt-in "brand" hue band -- blue -> violet, degrees on the
 * OKLCH hue wheel. Deliberately excludes every hue the score color ramp
 * (`lib/scoreColor.ts`) uses for score quality (green/amber/red-orange), so
 * a brand-toned sigil can never be misread as a performance signal. Both
 * endpoints are WCAG-1.4.11-verified to clear 3:1 against #ffffff at
 * L=0.62 C=0.11 (see components/brand/README.md for the computed ratios) --
 * if this band or the fixed L/C in ScoreSigil.tsx ever change, re-verify.
 */
export const BRAND_HUE_START_DEG = 250;
export const BRAND_HUE_SPAN_DEG = 60;

/**
 * Maps the full-wheel, hash-derived `hue` (0-360, see `sigilParams`) into
 * the constrained brand band. Per-company variation survives -- two
 * tickers with different hashes still land on different points in the
 * band -- but the mapping can never produce a red, green, amber, or olive
 * hue, because the band excludes them entirely by construction.
 */
export function brandHue(hue: number): number {
  return round3(BRAND_HUE_START_DEG + (mod360(hue) / 360) * BRAND_HUE_SPAN_DEG);
}

/**
 * The sigil's "brand" tone is fixed at this lightness/chroma (see
 * components/brand/README.md's WCAG 1.4.11 table) -- only the hue varies,
 * within `brandHue`'s band. Kept alongside `brandHue`/`brandHueHex` so the
 * three values that define the brand color never drift apart.
 */
const BRAND_L = 0.62;
const BRAND_C = 0.11;

function srgbGammaEncode(c: number): number {
  const clamped = c < 0 ? 0 : c > 1 ? 1 : c;
  return clamped <= 0.0031308
    ? clamped * 12.92
    : 1.055 * Math.pow(clamped, 1 / 2.4) - 0.055;
}

function toHexByte(c: number): string {
  const v = Math.round(srgbGammaEncode(c) * 255);
  const clamped = v < 0 ? 0 : v > 255 ? 255 : v;
  return clamped.toString(16).padStart(2, "0");
}

/**
 * Converts an OKLCH color to a 6-digit sRGB hex string (`#rrggbb`), using
 * the standard OKLab <-> linear-sRGB matrices (Björn Ottosson's OKLab
 * reference conversion). Pure math, no dependency -- this is a *notation*
 * change only: Satori (the renderer behind next/og's `ImageResponse`)
 * silently drops `oklch()` color strings (confirmed empirically -- see
 * components/brand/README.md and reports/agents/2026-08-17_fe-phase6-share-layer.md),
 * so every consumer of the sigil's "brand" tone needs a hex value that
 * renders identically in both the DOM and Satori, not two different color
 * pipelines.
 */
export function oklchToHex(l: number, c: number, hueDeg: number): string {
  const hRad = (hueDeg * Math.PI) / 180;
  const a = c * Math.cos(hRad);
  const b = c * Math.sin(hRad);

  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;

  const l3 = l_ * l_ * l_;
  const m3 = m_ * m_ * m_;
  const s3 = s_ * s_ * s_;

  const r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3;
  const g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3;
  const bl = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3;

  return `#${toHexByte(r)}${toHexByte(g)}${toHexByte(bl)}`;
}

/**
 * The sigil's "brand" tone, resolved to hex: folds the full-wheel,
 * hash-derived `hue` into the constrained band (`brandHue`) and converts at
 * the band's fixed `L=0.62 C=0.11` (see components/brand/README.md's WCAG
 * 1.4.11 table -- both endpoints and everything between clear 3:1 against
 * #ffffff at this L/C). This is the only place "brand" tone color is
 * produced; ScoreSigil.tsx calls this instead of emitting oklch() itself.
 */
export function brandHueHex(hue: number): string {
  return oklchToHex(BRAND_L, BRAND_C, brandHue(hue));
}

function clamp01(n: number): number {
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function mod360(n: number): number {
  const m = n % 360;
  return m < 0 ? m + 360 : m;
}

/**
 * Derives the sigil's visual parameters. The ticker hash selects stable
 * per-company properties (hue, base rotation); the score and subscores
 * drive continuous geometry (ring fill-arc, spoke lengths). Categories are
 * spaced evenly by angle starting from the base rotation, in the order
 * `subscores` is given.
 */
export function sigilParams(
  ticker: string,
  score: number | null,
  subscores: (number | null)[],
): SigilParams {
  const hash = hashTicker(ticker);
  // Two decorrelated properties from one hash: low byte for hue, next byte
  // for rotation. Both are plain integer ops -- no locale, no randomness.
  const hue = round3((hash & 0xff) * (360 / 256));
  const baseRotationDeg = round3(((hash >>> 8) & 0xff) * (360 / 256));

  const ringAbsent = score === null;
  const ringFillFrac = ringAbsent ? 0 : round3(clamp01(score / 100));

  const n = subscores.length;
  const spokes: SigilSpoke[] = subscores.map((s, i) => {
    const angleDeg = round3(
      mod360(baseRotationDeg + i * (n === 0 ? 0 : 360 / n)),
    );
    const absent = s === null;
    const lengthFrac = absent ? 0 : round3(clamp01(s / 100));
    return { angleDeg, lengthFrac, absent };
  });

  return {
    ticker,
    hue,
    baseRotationDeg,
    ringFillFrac,
    ringAbsent,
    spokes,
  };
}
