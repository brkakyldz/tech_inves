import Link from "next/link";
import { getScoreHighlights } from "@/lib/data/scores";
import { EmptyState } from "@/components/layout/EmptyState";
import { StaggerItem } from "@/components/motion/StaggerItem";
import { ScoreCard } from "./ScoreCard";

export async function ScoreHighlights() {
  const scores = await getScoreHighlights();

  // No scores at all -- no run has finished, or the API is unreachable
  // (lib/data/scores.ts degrades that to [] so the build works without a
  // back-end). The section keeps its heading, since the heading explains
  // what the reader would see here, and swaps the card grid for a stated
  // reason rather than an empty row of nothing.
  if (scores.length === 0) {
    return (
      <section className="section-y">
        <div className="content-wrap">
          <div className="prose-measure">
            <h2 className="font-serif text-3xl font-semibold tracking-tight">
              Highlighted scores from the latest run
            </h2>
          </div>
          <div className="mt-8">
            <EmptyState title="No run has produced scores yet">
              Once a scoring run finishes, the largest movements across the
              watchlist appear here. If the API is not running, start it and
              use <span className="font-medium">Refresh scores</span> above.
            </EmptyState>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <h2 className="font-serif text-3xl font-semibold tracking-tight">
            Highlighted scores from the latest run
          </h2>
          <p className="mt-3 text-muted-foreground text-pretty">
            A sample cross-section of the 40-company watchlist. Each score is
            a percentile ranking against companies in the same cohort — not
            an absolute scale.
          </p>
        </div>

        {/* Score-card grid must not inherit the prose measure -- it needs
            the full content width to lay out its columns. */}
        <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {scores.map((score, index) => (
            <StaggerItem key={score.ticker} index={index}>
              <ScoreCard score={score} />
            </StaggerItem>
          ))}
        </div>

        <div className="mt-6">
          <Link
            href="/screener"
            className="text-sm font-medium underline underline-offset-4 hover:text-foreground"
          >
            See all 40 companies&apos; scores &rarr;
          </Link>
        </div>
      </div>
    </section>
  );
}
