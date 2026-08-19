import type { Metadata } from "next";
import Link from "next/link";
import { EmptyState } from "@/components/layout/EmptyState";
import { ScreenerTable } from "@/components/screener/ScreenerTable";
import { getAllScores } from "@/lib/data/scores";
import { formatScore } from "@/lib/scoreColor";

export const metadata: Metadata = {
  title: "Screener — TechInves",
  description:
    "The full score table for the 40-company US technology watchlist: sort and filter by cohort and category.",
};

export default async function ScreenerPage() {
  const scores = await getAllScores();

  // Editorial practice (plan §7.2): the headline states the finding, not
  // the topic. Same "top vs. bottom spread" shape the landing hero uses,
  // computed over the full 40-company list rather than the highlighted
  // subset -- a legitimate, live finding about this exact table.
  const sorted = [...scores].sort((a, b) => b.compositeScore - a.compositeScore);
  const top = sorted[0];
  const bottom = sorted[sorted.length - 1];
  const spread = top && bottom ? top.compositeScore - bottom.compositeScore : null;

  return (
    <div>
      <section className="section-y">
        <div className="content-wrap">
          <div className="prose-measure">
            <h1 className="font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
              {spread !== null ? (
                <>
                  The watchlist spans a{" "}
                  <span className="font-mono tabular-nums">{formatScore(spread)}</span>
                  -point score gap
                </>
              ) : (
                "Every watchlist score, in one table"
              )}
            </h1>
            <p className="text-dek mt-4 text-muted-foreground text-pretty">
              {top && bottom ? (
                <>
                  From <span className="font-mono tabular-nums">{top.ticker}</span> at{" "}
                  <span className="font-mono tabular-nums">{formatScore(top.compositeScore)}</span>{" "}
                  to <span className="font-mono tabular-nums">{bottom.ticker}</span> at{" "}
                  <span className="font-mono tabular-nums">{formatScore(bottom.compositeScore)}</span>.{" "}
                </>
              ) : null}
              {scores.length > 0
                ? `The full composite score table for ${scores.length} watchlist companies. Filter by cohort, sort by any column.`
                : "The full composite score table for the US technology watchlist. Filter by cohort, sort by any column."}
            </p>
          </div>

          <div className="mt-10">
            {/* An empty list here is a real state, not a bug: with no API
                running (or before the first scoring run) `getAllScores()`
                returns [] rather than throwing, so the page must say why
                the table is empty instead of rendering headers over
                nothing. */}
            {scores.length > 0 ? (
              <ScreenerTable scores={scores} />
            ) : (
              <EmptyState title="No scores to show yet">
                Either no scoring run has finished yet, or the back-end API is
                not reachable from this site. Start the API, then run{" "}
                <span className="font-medium">Refresh scores</span> from the{" "}
                <Link href="/" className="underline underline-offset-4">
                  control panel
                </Link>
                .
              </EmptyState>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
