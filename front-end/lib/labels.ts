/**
 * Single source of truth for display labels used across the landing page
 * and score cards (audit item A8, plan §6 Phase 4 acceptance).
 *
 * Interface language is English (ADR 0010's product framing, plan §6 Faz 5
 * step 4). `CategoryRadarChart.tsx` imports `CATEGORY_LABEL` from here
 * rather than keeping its own map, so there is exactly one category-label
 * map in the codebase.
 */
import type {
  CategoryScore,
  Cohort,
  DeltaUnavailableReason,
  ScoreBand,
} from "./data/types";

export const CATEGORY_LABEL: Record<CategoryScore["name"], string> = {
  "Profitability & Quality": "Profitability & Quality",
  Growth: "Growth",
  Valuation: "Valuation",
  "Financial Health": "Financial Health",
};

export const COHORT_LABEL: Record<Cohort, string> = {
  A: "Software & Internet",
  B: "Hardware, Semiconductors & Space",
  C: "IT Services & Infrastructure",
};

// Composite/category score band display labels (same class of defect as
// A8 -- lib/scoreColor.ts's BAND_STYLES carries the same English labels
// used directly in the UI). `RiskBand` labels are deliberately NOT
// duplicated here: they live at the source (lib/scoreColor.ts's
// RISK_BAND_STYLES), which is a different, separate scale from ScoreBand.
// The `ScoreBand` keys below are the scoring engine's contract and must
// not change -- only the displayed strings were ever translated.
export const SCORE_BAND_LABEL: Record<ScoreBand, string> = {
  Strong: "Strong",
  Good: "Good",
  Moderate: "Moderate",
  Weak: "Weak",
  "Very Weak": "Very Weak",
};

/**
 * Why no run-to-run delta is being shown (plan §8 Faz 7a).
 *
 * Each label states the reason in the reader's terms, because "no change
 * shown" and "we will not claim a change" are different messages and the
 * second one is the honest one here. `deltaUnavailableLabel()` below falls
 * back rather than indexing blind: a reason string this front end does not
 * recognise must still say *something*, never render as a number and never
 * render as empty.
 */
export const DELTA_UNAVAILABLE_LABEL: Record<DeltaUnavailableReason, string> = {
  first_run: "No earlier run to compare against",
  unknown_provenance: "Previous run's inputs are unknown",
  incomplete_run: "A run in the comparison did not complete",
  cohort_changed: "Cohort changed between runs",
  regime_changed: "Scoring regime changed between runs",
};

export const DELTA_UNAVAILABLE_FALLBACK = "Not comparable to the previous run";

export function deltaUnavailableLabel(reason: string | null): string {
  if (reason === null) return DELTA_UNAVAILABLE_FALLBACK;
  return (
    DELTA_UNAVAILABLE_LABEL[reason as DeltaUnavailableReason] ??
    DELTA_UNAVAILABLE_FALLBACK
  );
}
