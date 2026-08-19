import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GRAIN_BACKGROUND_STYLE } from "@/components/brand/grain";
import { getScoreHighlights } from "@/lib/data/scores";
import { formatScore } from "@/lib/scoreColor";

interface HeadlineData {
  ticker: string;
  score: number;
  spread: number;
}

// Editorial practice (plan §7.2): the headline states the finding, not the
// topic. It reads the current score snapshot and leads with the top
// performer and the spread between the best and worst of the featured set
// -- both are legitimate "notable movement" candidates given what
// /v1/scores/highlights actually returns (a snapshot, no run-over-run delta
// field yet -- see plan §8 Faz 7a). Falls back to a safe, non-data-dependent
// finding-shaped sentence if the fetch fails or the set is empty, so the
// hero never renders half a claim.
//
// ADR 0010 §1: this is now a self-hosted, on-demand personal tool, not a
// published weekly digest -- the headline states what the last run found,
// not "this week's" anything, and the CTAs point at browsing the existing
// data rather than "reading" a scheduled release.
async function getHeadlineData(): Promise<HeadlineData | null> {
  try {
    const scores = await getScoreHighlights();
    if (scores.length === 0) return null;
    const sorted = [...scores].sort((a, b) => b.compositeScore - a.compositeScore);
    const top = sorted[0];
    const bottom = sorted[sorted.length - 1];
    return {
      ticker: top.ticker,
      score: top.compositeScore,
      spread: top.compositeScore - bottom.compositeScore,
    };
  } catch {
    return null;
  }
}

export async function Hero() {
  const headline = await getHeadlineData();

  return (
    <section className="section-y relative overflow-hidden">
      {/* Ornament budget: one per viewport (plan §7.2). The grain is the
          hero's single ornament -- no sigil or chart competes with it here. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={GRAIN_BACKGROUND_STYLE}
      />
      <div className="content-wrap relative">
        <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
          US Technology Sector &middot; On demand
        </p>
        {headline ? (
          <h1 className="text-hero mt-4 max-w-3xl font-serif font-semibold text-balance">
            <span className="font-mono tabular-nums">{headline.ticker}</span> leads the latest run
            at <span className="font-mono tabular-nums">{formatScore(headline.score)}</span>
          </h1>
        ) : (
          <h1 className="text-hero mt-4 max-w-3xl font-serif font-semibold text-balance">
            Cohort-relative performance across the US technology watchlist
          </h1>
        )}
        <p className="text-dek mt-6 text-muted-foreground text-pretty">
          {headline
            ? `A ${formatScore(headline.spread)}-point spread separates the featured scores. Software & Internet, Hardware & Semiconductors, and IT Services cohorts, scored 0-100 by within-cohort percentile rank.`
            : "Software & Internet, Hardware & Semiconductors, and IT Services cohorts, scored 0-100 by within-cohort percentile rank."}
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <Button asChild size="lg">
            <Link href="/screener">Browse all scores</Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link href="/legal">Read the methodology</Link>
          </Button>
        </div>
      </div>
    </section>
  );
}
