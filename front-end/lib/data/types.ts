export type Cohort = "A" | "B" | "C";

export type ScoreBand = "Strong" | "Good" | "Moderate" | "Weak" | "Very Weak";

// Risk sub-score band (src/techinves/scoring/risk.py::risk_band) -- a
// separate scale from ScoreBand. "No data" means no risk component was
// computable, distinct from a measured "High risk".
export type RiskBand =
  | "Solid"
  | "Adequate"
  | "Worth watching"
  | "Fragile"
  | "High risk"
  | "No data";

export interface CategoryScore {
  name: "Profitability & Quality" | "Growth" | "Valuation" | "Financial Health";
  score: number | null; // 0-100, null = no metric in this category was computable
  weight: number; // 0-1, within-cohort weight
}

// Mirrors src/techinves/api/schemas.py::RiskOut. Only present on the company
// detail response, not on list/highlight items.
export interface RiskScore {
  score: number | null; // null = no risk component was computable; band is "No data"
  band: RiskBand;
  altmanZ: number | null;
  altmanZone: string;
  piotroskiF: number | null;
  netDebtEbitda: number | null;
  interestCoverage: number | null;
  cashRunwayMonths: number | null;
  burnMultiple: number | null;
  dilutionYoyPct: number | null;
  componentsUsed: string[];
}

// Why a delta is not being shown (src/techinves/api/repositories.py's
// DELTA_* constants). Deliberately widened with `(string & {})` rather than
// closed: an unrecognised reason from a newer back end must still render as
// "unavailable", never fall through to a number.
export type DeltaUnavailableReason =
  | "first_run"
  | "unknown_provenance"
  | "incomplete_run"
  | "cohort_changed"
  | "regime_changed";

// Mirrors src/techinves/api/schemas.py::ScoreDeltaOut. Change in composite
// score since the previous run that scored this company.
//
// `delta === null` XOR `unavailableReason === null`. There is no third state
// and no zero fallback: 0 means *measured, and unchanged*, which is a
// different claim from *not comparable*. Callers must branch on null rather
// than defaulting -- see components/motion/DeltaIndicator.tsx.
export interface ScoreDelta {
  delta: number | null;
  previousComposite: number | null;
  previousRunId: string | null;
  currentRunId: string;
  unavailableReason: DeltaUnavailableReason | (string & {}) | null;
}

export interface ScoreBlock {
  ticker: string;
  companyName: string;
  cohort: Cohort;
  compositeScore: number; // 0-100
  band: ScoreBand;
  categories: CategoryScore[];
  sectorPercentile: number; // 0-100
  coveragePct: number; // 0-1, fraction of expected metrics that were computable
  lowReliability: boolean; // true when coveragePct is below the reliability threshold
  // Run-to-run change (plan §8 Faz 7a). Always present on list, highlight
  // and detail responses; carries its own reason when there is no number.
  delta: ScoreDelta;
  risk?: RiskScore; // present on company detail responses, absent on list items
}

// Mirrors src/techinves/api/schemas.py::VerifierViolationOut, which mirrors
// pipeline/schemas.py::VerifierViolation. `severity` is deliberately a plain
// string rather than a union: the value is read back out of a JSON column
// that older pipeline revisions wrote, and an unrecognised severity must be
// rendered (at the loudest treatment), never dropped for failing to match a
// literal type.
export interface VerifierViolation {
  severity: string; // compliance_hard | structural_hard | soft
  category: string;
  message: string;
  section: string | null;
}

// The verifier's four-valued verdict (pipeline/verifier/node.py). `null` is a
// fifth state and is NOT a synonym for "pass": it means no verdict was ever
// recorded for this report. See lib/verifier/banner.ts.
export type VerifierVerdict =
  | "pass"
  | "pass_with_flags"
  | "degraded_publish"
  | "block";

export interface ReportSummary {
  slug: string;
  // Mirrors src/techinves/api/schemas.py::ReportSummaryOut. Keyed on the run
  // that produced the report, not an ISO week (ADR 0010 §2); `slug` is
  // derived from `runId`.
  runId: string;
  createdAt: string; // ISO datetime
  title: string;
  excerpt: string;
  highlightedTickers: string[];
  // ADR 0010 §6. Typed as `VerifierVerdict | string | null` because the API
  // is the source of truth for this vocabulary and the UI must not silently
  // swallow a verdict it has not been taught about.
  verifierVerdict: VerifierVerdict | string | null;
  isPartial: boolean;
}

// Mirrors src/techinves/api/schemas.py::ReportSectionOut. One row per
// company (section_type="company", ticker set) or per macro topic
// (section_type="macro", topic set) -- see REPORT_SPEC.md §2/§7 (W7).
export interface ReportSection {
  sectionType: "company" | "macro";
  ticker: string | null;
  topic: string | null;
  title: string;
  bodyMarkdown: string;
  orderIndex: number;
}

// Mirrors src/techinves/api/schemas.py::ReportDetailOut -- what
// `/v1/reports/{slug}` actually returns (a superset of ReportSummary).
// `/v1/reports/latest` and the list endpoint return plain ReportSummary
// with no `sections`.
export interface ReportDetail extends ReportSummary {
  sections: ReportSection[];
  // ADR 0010 §6: what the warning banner *names*. `null` = no verifier
  // report was stored for this row; `[]` = the verifier ran and found
  // nothing. Distinct states, kept distinct.
  verifierViolations: VerifierViolation[] | null;
}

export interface ScoreHistoryPoint {
  period: string; // display label -- the point's generation date
  runId: string; // the run that produced this point (ADR 0010 §2)
  compositeScore: number;
}
