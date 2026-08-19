import type { RiskBand, ScoreBand } from "./data/types";

interface BandStyle {
  label: string;
  text: string;
  bg: string;
  border: string;
  bar: string;
}

// Every color value here is a Tailwind v4 arbitrary value pointing at a
// CSS custom property defined in the plain `:root` block of globals.css
// (`--score-*`), not a generated `@theme inline` utility -- those get
// tree-shaken when nothing statically references them (confirmed on
// --chart-1/--color-chart-1 in Phase 1). Referencing var(--score-strong)
// directly here means the class always compiles, whether or not any
// other utility "claims" the token first.
//
// The five tiers form one continuous sequential ramp (blue -> amber ->
// red-orange, see globals.css) rather than five unrelated hues, so the
// *order* of Strong..Very Weak reads correctly under red-green color
// vision deficiency, which the previous emerald/teal/amber/orange/red
// mapping did not guarantee (red-green is the dominant CVD pattern).
const RAMP_STYLES: Record<
  "strong" | "good" | "moderate" | "weak" | "very-weak",
  BandStyle
> = {
  strong: {
    label: "Strong",
    text: "text-[var(--score-strong)]",
    bg: "bg-[var(--score-strong-bg)]",
    border: "border-[var(--score-strong-border)]",
    bar: "bg-[var(--score-strong)]",
  },
  good: {
    label: "Good",
    text: "text-[var(--score-good)]",
    bg: "bg-[var(--score-good-bg)]",
    border: "border-[var(--score-good-border)]",
    bar: "bg-[var(--score-good)]",
  },
  moderate: {
    label: "Moderate",
    text: "text-[var(--score-moderate)]",
    bg: "bg-[var(--score-moderate-bg)]",
    border: "border-[var(--score-moderate-border)]",
    bar: "bg-[var(--score-moderate)]",
  },
  weak: {
    label: "Weak",
    text: "text-[var(--score-weak)]",
    bg: "bg-[var(--score-weak-bg)]",
    border: "border-[var(--score-weak-border)]",
    bar: "bg-[var(--score-weak)]",
  },
  "very-weak": {
    label: "Very Weak",
    text: "text-[var(--score-very-weak)]",
    bg: "bg-[var(--score-very-weak-bg)]",
    border: "border-[var(--score-very-weak-border)]",
    bar: "bg-[var(--score-very-weak)]",
  },
};

const BAND_STYLES: Record<ScoreBand, BandStyle> = {
  Strong: RAMP_STYLES.strong,
  Good: RAMP_STYLES.good,
  Moderate: RAMP_STYLES.moderate,
  Weak: RAMP_STYLES.weak,
  "Very Weak": RAMP_STYLES["very-weak"],
};

export function scoreBandStyle(band: ScoreBand): BandStyle {
  return BAND_STYLES[band];
}

// Risk sub-score bands (src/techinves/scoring/risk.py::risk_band) -- a
// separate scale from ScoreBand, so it gets its own style map rather than
// being derived from the composite band. Solid..High risk mirror the same
// 5-tier ramp used above (best -> worst), reusing the identical tokens so
// "risk" and "composite score" don't invent a second, competing palette.
// "No data" is a genuinely distinct, neutral state, not a synonym for
// "High risk" -- it deliberately stays on the existing neutral
// muted/border tokens rather than joining the ramp, so a viewer can never
// mistake "we couldn't measure this" for "this measured badly".
const RISK_BAND_STYLES: Record<RiskBand, BandStyle> = {
  Solid: { ...RAMP_STYLES.strong, label: "Low risk" },
  Adequate: { ...RAMP_STYLES.good, label: "Low risk" },
  "Worth watching": { ...RAMP_STYLES.moderate, label: "Moderate risk" },
  Fragile: { ...RAMP_STYLES.weak, label: "High risk" },
  "High risk": { ...RAMP_STYLES["very-weak"], label: "High risk" },
  "No data": {
    label: "No data",
    text: "text-muted-foreground",
    bg: "bg-muted/40",
    border: "border-border",
    bar: "bg-muted-foreground",
  },
};

export function riskBandStyle(band: RiskBand): BandStyle {
  return RISK_BAND_STYLES[band];
}

export function scoreToBand(score: number): ScoreBand {
  if (score >= 80) return "Strong";
  if (score >= 65) return "Good";
  if (score >= 45) return "Moderate";
  if (score >= 30) return "Weak";
  return "Very Weak";
}

// FMP-derived scores/percentiles carry long floating-point tails (e.g.
// 65.58139534883721) -- round to a display-friendly precision everywhere
// a raw score value is rendered. null means the underlying category/risk
// sub-score had no computable data -- render "N/A" rather than a fake 0.0.
export function formatScore(value: number | null): string {
  return value === null ? "N/A" : value.toFixed(1);
}

export function formatPercentile(value: number): string {
  return Math.round(value).toString();
}

// R27: a 55%-coverage 84 and a 98%-coverage 84 previously rendered
// identically everywhere the composite score appears -- coverage was only
// ever named in Coverage Notes prose. `coveragePct` is 0-1 (fraction of
// expected metrics that were computable).
export const LOW_RELIABILITY_COVERAGE_THRESHOLD = 0.6;

export function formatCoveragePct(value: number): string {
  return `${Math.round(value * 100)}%`;
}
