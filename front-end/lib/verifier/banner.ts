/**
 * The verifier warning banner's decision layer (ADR 0010 §6, plan Faz 5.3).
 *
 * ADR 0010 §6 relaxed a safety property: a blocked draft is no longer
 * withheld from the reader, it is rendered *with its violations named*. That
 * ADR's Consequences section is explicit about what the banner therefore is —
 * "the only thing standing between a blocked draft and being read as a
 * finished one", and so "a correctness-relevant UI element, not decoration".
 *
 * All of the judgement lives here, in a pure function over plain data, and
 * none of it lives in the component. Two reasons, both practical: this file
 * is testable without a DOM or a React renderer, and a future contributor
 * changing what a verdict means has one place to change it rather than a
 * template full of ternaries.
 *
 * ## The verdict vocabulary is five-valued
 *
 * `pipeline/verifier/node.py` produces four verdicts. The database adds a
 * fifth state by being able to hold none of them.
 *
 * | verdict            | what it means                                   | treatment |
 * |--------------------|-------------------------------------------------|-----------|
 * | `block`            | a compliance-hard violation: a fabricated number, a fabricated citation, a missing legal disclaimer or AI disclosure | `critical` |
 * | `degraded_publish` | structural-hard only: the report is missing coverage it promised, but nothing about it is untrustworthy | `serious` |
 * | `pass_with_flags`  | soft issues: a missing low-reliability label, a thin section count | `advisory` |
 * | `pass`             | nothing                                          | none (unless partial) |
 * | `null`             | no verdict was recorded                          | `unknown` |
 *
 * Three of those are worth justifying, because ADR 0010 §6 and plan §6.3
 * discuss only `block`:
 *
 * - **`degraded_publish` gets a banner too.** It is precisely the
 *   "renderable, but must carry a banner" state — `pipeline/run.py` has been
 *   publishing these reports all along. If it rendered clean, structural-hard
 *   violations would ship silently, and the reader would have no way to know
 *   the report covers less than it appears to. It is nonetheless *not*
 *   `block`: nothing in a degraded report is fabricated, so treating the two
 *   identically would train the reader to discount both. Distinct wording,
 *   distinct label, one step quieter.
 *
 * - **`pass_with_flags` gets something visible but calm.** Soft violations
 *   are real information (a score whose data coverage is too thin to rely on)
 *   but they are not a reason to distrust the document. Rendering them at
 *   alarm volume is how a banner becomes wallpaper, and a banner that is
 *   wallpaper no longer does the one job ADR 0010 gave it.
 *
 * - **`null` is not `pass`.** A report saved before a verdict existed, or
 *   written by a run that died mid-flight, has never been checked. Rendering
 *   it clean would be the identical failure to suppressing a block — a
 *   reader concluding "verified" from the absence of a warning — just in a
 *   different disguise. Absence of evidence is shown as absence of evidence.
 *
 * An unrecognised verdict string (a vocabulary this build has not been taught)
 * is treated as `critical`. Failing loud on an unknown verdict is the only
 * direction of error that cannot mislead.
 */

import type { VerifierViolation } from "@/lib/data/types";

/**
 * How loudly the banner speaks. Ordered, and the order is meaningful:
 * `critical` > `serious` > `unknown` > `advisory`.
 *
 * `unknown` sits above `advisory` deliberately. "We do not know whether this
 * report is sound" is a stronger warning than "the verifier found some minor
 * issues", because the second at least tells you the checks ran.
 */
export type BannerLevel = "critical" | "serious" | "unknown" | "advisory";

export interface BannerItem {
  /** Short, human-readable class of problem — the badge text. */
  label: string;
  /** The violation text itself, as the verifier wrote it. */
  message: string;
  /** Ticker or topic the violation was attributed to, when it could be. */
  section: string | null;
}

export interface BannerModel {
  level: BannerLevel;
  /** The heading — states the report's status in one clause. */
  title: string;
  /** One or two sentences: what the level means for how to read this. */
  explanation: string;
  /** Prefix for the violation list, e.g. "3 issues found by the verifier:". */
  itemsHeading: string;
  items: BannerItem[];
  /**
   * True when there are more violations than `items` shows. The banner never
   * truncates — this stays false — but the field exists so a future change
   * that *does* truncate has to say so out loud rather than silently.
   */
  truncated: boolean;
}

export interface BannerInput {
  verdict: string | null | undefined;
  violations: VerifierViolation[] | null | undefined;
  isPartial: boolean;
}

/**
 * Human-readable names for `pipeline.schemas.VerifierViolation.category`.
 * An unmapped category falls back to the raw value rather than to a generic
 * word: a reader seeing `low_reliability_label` learns more than one seeing
 * "Issue".
 */
const CATEGORY_LABELS: Record<string, string> = {
  number_leak: "Unverifiable number",
  citation: "Fabricated citation",
  disclaimer: "Missing disclaimer",
  completeness: "Incomplete coverage",
  low_reliability_label: "Missing reliability label",
};

const SEVERITY_RANK: Record<string, number> = {
  compliance_hard: 0,
  structural_hard: 1,
  soft: 2,
};

function labelFor(violation: VerifierViolation): string {
  return CATEGORY_LABELS[violation.category] ?? violation.category;
}

/** Compliance-hard first, then structural, then soft; stable within a rank. */
function bySeverity(a: VerifierViolation, b: VerifierViolation): number {
  const ra = SEVERITY_RANK[a.severity] ?? 0; // unknown severity sorts to the top
  const rb = SEVERITY_RANK[b.severity] ?? 0;
  return ra - rb;
}

const KNOWN_VERDICTS = new Set([
  "pass",
  "pass_with_flags",
  "degraded_publish",
  "block",
]);

/**
 * The banner to render above a report, or `null` when there is genuinely
 * nothing to say — which is only the case for a `pass` verdict on a report
 * that covered the whole watchlist.
 *
 * Note what this function does *not* take: no user preference, no dismissal
 * state, no "seen before" flag, no request context. It cannot be told to
 * return `null`; the only input that produces `null` is a clean report. That
 * is the non-suppressibility guarantee, expressed as a type signature rather
 * than as a promise in a comment — and `lib/verifier/banner.test.mjs` asserts
 * the signature stays that shape.
 */
export function buildBannerModel(input: BannerInput): BannerModel | null {
  const verdict = input.verdict ?? null;
  const violations = input.violations ?? null;
  const known = verdict !== null && KNOWN_VERDICTS.has(verdict);

  const items: BannerItem[] = (violations ?? [])
    .slice()
    .sort(bySeverity)
    .map((v) => ({
      label: labelFor(v),
      message: v.message,
      section: v.section,
    }));

  if (input.isPartial) {
    // Plan §2.5: a run whose ticker set is smaller than the watchlist has to
    // say so. It is listed as an item rather than as separate furniture so
    // there is exactly one place on the page a reader has to look to learn
    // what is wrong with the document.
    items.push({
      label: "Partial run",
      message:
        "This run covered fewer tickers than the full watchlist, so the report is not a complete sector view.",
      section: null,
    });
  }

  const itemsHeading = itemsHeadingFor(items.length, violations);

  if (!known) {
    if (verdict === null) {
      return {
        level: "unknown",
        title: "This report has no verifier verdict",
        explanation:
          "No verifier result was recorded for this run, so nothing here has been checked for fabricated numbers, fabricated citations or missing disclosures. Treat it as unverified — an absent verdict is not a passing one.",
        itemsHeading,
        items,
        truncated: false,
      };
    }
    return {
      level: "critical",
      title: `This report carries an unrecognised verifier verdict (${verdict})`,
      explanation:
        "This build of the interface does not know what this verdict means, so it is shown at the highest severity rather than assumed to be safe. Do not rely on the contents until the verdict has been checked against the pipeline.",
      itemsHeading,
      items,
      truncated: false,
    };
  }

  if (verdict === "block") {
    return {
      level: "critical",
      title: "This report was blocked by the verifier",
      explanation:
        "The verifier found at least one trust-and-safety violation — a number or citation it could not trace to a source, or a required disclosure that is missing. The draft is shown so you can see what the pipeline produced, not because it is fit to rely on. Do not treat any figure or link below as checked.",
      itemsHeading,
      items,
      truncated: false,
    };
  }

  if (verdict === "degraded_publish") {
    return {
      level: "serious",
      title: "This report is published with known gaps",
      explanation:
        "The verifier found no trust-and-safety problem, so what is here traces back to a source. It did find structural gaps: the report is missing coverage it was supposed to include. Read it as an incomplete view, not a wrong one.",
      itemsHeading,
      items,
      truncated: false,
    };
  }

  if (verdict === "pass_with_flags") {
    return {
      level: "advisory",
      title: "This report passed with flags",
      explanation:
        "The verifier found no hard violations. The minor issues below are worth knowing about before you read too much into any single number.",
      itemsHeading,
      items,
      truncated: false,
    };
  }

  // verdict === "pass".
  if (input.isPartial) {
    return {
      level: "advisory",
      title: "This report passed, but covers only part of the watchlist",
      explanation:
        "The verifier found no issues with what is here. What is here is less than a full sector view.",
      itemsHeading,
      items,
      truncated: false,
    };
  }

  // A clean verdict on a complete run — and the only path that renders
  // nothing. Note that `pass` with a non-empty violation list is impossible
  // by construction in pipeline/verifier/node.py (any violation raises the
  // verdict), but if one ever arrived it would still be listed below by the
  // `pass_with_flags` branch rather than swallowed here.
  if (items.length > 0) {
    return {
      level: "advisory",
      title: "This report passed, with notes",
      explanation:
        "The verifier returned a passing verdict but also recorded the notes below, which is not a combination the pipeline is expected to produce. Worth a look.",
      itemsHeading,
      items,
      truncated: false,
    };
  }
  return null;
}

function itemsHeadingFor(
  count: number,
  violations: VerifierViolation[] | null,
): string {
  if (count === 0) {
    return violations === null
      ? "No violation detail was stored for this report."
      : "The verifier recorded no specific violations.";
  }
  return count === 1 ? "1 issue recorded:" : `${count} issues recorded:`;
}
