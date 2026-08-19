import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CategoryRadarChart } from "@/components/companies/CategoryRadarChart";
import { CoverageBadge } from "@/components/companies/CoverageBadge";
import { ScoreHistoryChart } from "@/components/companies/ScoreHistoryChart";
import { ScoreCounter } from "@/components/motion/ScoreCounter";
import { ScoreDeltaDisplay } from "@/components/motion/DeltaIndicator";
import { getAllScores, getScoreByTicker, getScoreHistory } from "@/lib/data/scores";
import { formatPercentile, formatScore, riskBandStyle, scoreBandStyle } from "@/lib/scoreColor";
import { COHORT_LABEL, CATEGORY_LABEL, SCORE_BAND_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";

// Kept explicit rather than left to the default: it is what makes an empty
// `generateStaticParams` safe. With no API running at build time
// `getAllScores()` returns [] (lib/data/scores.ts), so no ticker page is
// prerendered -- every one of them is rendered on demand instead, and the
// tag-based revalidation contract (lib/api/client.ts) is unchanged.
export const dynamicParams = true;

export async function generateStaticParams() {
  const scores = await getAllScores();
  return scores.map((s) => ({ ticker: s.ticker }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ ticker: string }>;
}): Promise<Metadata> {
  const { ticker } = await params;
  let score: Awaited<ReturnType<typeof getScoreByTicker>>;
  try {
    score = await getScoreByTicker(ticker.toUpperCase());
  } catch {
    // `generateMetadata` runs *outside* the segment's error boundary, so a
    // throw here is not the styled `app/error.tsx` -- it is a bare
    // "Internal Server Error" body with no document at all, which is
    // exactly the failure mode that boundary exists to remove. The page
    // body below deliberately still throws on the same failure; letting
    // it, rather than this, be what surfaces is the whole point.
    //
    // Not "Company not found": an unreachable API is not a claim that the
    // company does not exist. The neutral site title says nothing either
    // way.
    return { title: "TechInves" };
  }
  if (!score) return { title: "Company not found — TechInves" };
  return {
    title: `${score.ticker} — ${score.companyName} — TechInves`,
    description: `Composite financial score, category breakdown, and score history for ${score.companyName} (${score.ticker}).`,
  };
}

export default async function CompanyDetailPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  const score = await getScoreByTicker(ticker.toUpperCase());
  if (!score) notFound();

  const history = await getScoreHistory(score.ticker);
  // Delta emphasis (motion effect d) now reads the API's run-to-run delta
  // (plan §8 Faz 7a) instead of subtracting the last two points of the
  // history series here. The series carries only a period label and a score,
  // so a difference taken from it cannot tell a comparable pair from an
  // incomparable one -- it would have happily differenced across a failed
  // run, a cohort change, or ADR 0009's data-source seam, and reported the
  // result as a fact about the company. `score.delta` carries the reason
  // when there is no number; `ScoreDeltaDisplay` renders whichever it is.
  const band = scoreBandStyle(score.band);
  // Risk badge must reflect the risk sub-score's own band, not the
  // composite band -- a company can be a strong composite performer while
  // still carrying real balance-sheet/liquidity risk, and vice versa.
  const riskBand = score.risk ? riskBandStyle(score.risk.band) : riskBandStyle("No data");

  return (
    <div className="section-y content-wrap">
      <Link href="/screener" className="text-sm text-muted-foreground hover:text-foreground">
        &larr; Back to the screener
      </Link>

      <div className="mt-6 flex flex-wrap items-start justify-between gap-6">
        <div className="prose-measure">
          <p className="font-mono text-sm text-muted-foreground">{score.ticker}</p>
          {/* Editorial head (plan §7.2): the headline states the finding --
              where this company sits in its cohort -- not just its name. */}
          <h1 className="font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            {score.companyName}
          </h1>
          <p className="text-dek mt-4 text-muted-foreground text-pretty">
            In the {COHORT_LABEL[score.cohort]} cohort, at the{" "}
            <span className="font-mono tabular-nums">{formatPercentile(score.sectorPercentile)}%</span>{" "}
            sector percentile, composite score{" "}
            <span className="font-mono tabular-nums">{formatScore(score.compositeScore)}</span>.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Badge variant="outline" className={cn(band.text, band.border)}>
            {SCORE_BAND_LABEL[score.band]}
          </Badge>
          <Badge variant="outline" className={cn(riskBand.text, riskBand.border)}>
            {riskBand.label}
          </Badge>
        </div>
      </div>

      <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-3">
        <Card className={cn("sm:col-span-1", band.bg)}>
          <CardContent className="flex h-full flex-col items-center justify-center py-10">
            <ScoreCounter
              value={score.compositeScore}
              minWidthCh={5}
              className="text-center font-mono text-5xl font-semibold tabular-nums"
            />
            <span className="mt-1 text-sm text-muted-foreground">/ 100 composite score</span>
            <div className="mt-2 flex flex-col items-center gap-0.5 text-center">
              <span className="text-xs text-muted-foreground">vs. previous run</span>
              <ScoreDeltaDisplay
                delta={score.delta}
                format={(v) => v.toFixed(1)}
                unavailableClassName="text-xs text-balance"
              />
            </div>
            <div className="mt-2">
              <CoverageBadge coveragePct={score.coveragePct} />
            </div>
          </CardContent>
        </Card>

        <Card className="sm:col-span-2">
          <CardHeader>
            <p className="text-sm font-medium">Category breakdown</p>
          </CardHeader>
          <CardContent>
            <CategoryRadarChart categories={score.categories} />
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader>
          <p className="text-sm font-medium">Category detail</p>
        </CardHeader>
        <CardContent className="space-y-3">
          {score.categories.map((category) => (
            <div key={category.name}>
              <div className="flex items-center justify-between text-sm">
                <span>{CATEGORY_LABEL[category.name] ?? category.name}</span>
                <span className="font-mono tabular-nums text-muted-foreground">
                  <ScoreCounter value={category.score} minWidthCh={4} />
                  {" "}&middot; weight {Math.round(category.weight * 100)}%
                </span>
              </div>
              <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-border">
                <div
                  className={cn("h-full rounded-full", category.score === null ? "bg-transparent" : band.bar)}
                  style={{ width: `${category.score ?? 0}%` }}
                />
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {history.length > 0 && (
        <Card className="mt-6">
          <CardHeader>
            <p className="text-sm font-medium">Score history</p>
          </CardHeader>
          <CardContent>
            <ScoreHistoryChart points={history} />
          </CardContent>
        </Card>
      )}

      <p className="mt-8 text-xs text-muted-foreground text-pretty">
        This score is a deterministic screening tool based on publicly
        available financial data and is not investment advice. For details,
        see the{" "}
        <Link href="/legal" className="underline">
          methodology page
        </Link>
        .
      </p>
    </div>
  );
}
