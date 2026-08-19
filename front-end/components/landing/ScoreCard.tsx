import Link from "next/link";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CoverageBadge } from "@/components/companies/CoverageBadge";
import { ScoreSigil } from "@/components/brand/ScoreSigil";
import { ScoreCounter } from "@/components/motion/ScoreCounter";
import { ScoreDeltaDisplay } from "@/components/motion/DeltaIndicator";
import { formatPercentile, scoreBandStyle } from "@/lib/scoreColor";
import { COHORT_LABEL, CATEGORY_LABEL, SCORE_BAND_LABEL } from "@/lib/labels";
import type { ScoreBlock } from "@/lib/data/types";
import { cn } from "@/lib/utils";

export function ScoreCard({ score }: { score: ScoreBlock }) {
  const band = scoreBandStyle(score.band);

  return (
    <Link href={`/companies/${score.ticker}`} className="block">
      <Card className={cn("border transition-shadow hover:shadow-md", band.border, band.bg)}>
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div className="flex items-start gap-2">
            {/* The sigil's own hue channel never encodes score quality
                (components/brand/README.md); default tone="neutral" draws
                in currentColor so it never competes with the band badge's
                color, which is the card's ornament budget for this
                viewport. */}
            <ScoreSigil
              ticker={score.ticker}
              score={score.compositeScore}
              subscores={score.categories.map((c) => c.score)}
              size="sm"
              className="mt-0.5 size-8 shrink-0 text-muted-foreground"
            />
            <div>
              <p className="font-mono text-sm font-semibold tabular-nums">{score.ticker}</p>
              <p className="text-sm text-muted-foreground">{score.companyName}</p>
            </div>
          </div>
          <Badge variant="outline" className={cn("shrink-0", band.text, band.border)}>
            {SCORE_BAND_LABEL[score.band]}
          </Badge>
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline gap-2">
            <ScoreCounter
              value={score.compositeScore}
              minWidthCh={5}
              className="font-mono text-4xl font-semibold tabular-nums"
            />
            <span className="text-sm text-muted-foreground">/ 100</span>
            <CoverageBadge coveragePct={score.coveragePct} />
          </div>
          {/* Run-to-run change (plan §8 Faz 7a). On its own line rather than
              inline with the counter: the unavailable state is a sentence,
              not a glyph, and it must not be squeezed out of the layout. */}
          <p className="mt-1 flex items-center gap-1 text-xs">
            <span className="text-muted-foreground">vs. previous run</span>
            <ScoreDeltaDisplay
              delta={score.delta}
              className="text-xs"
              format={(v) => v.toFixed(1)}
            />
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {COHORT_LABEL[score.cohort]} &middot; Sector{" "}
            <span className="font-mono tabular-nums">{formatPercentile(score.sectorPercentile)}</span> percentile
          </p>

          <div className="mt-4 space-y-2">
            {score.categories.map((category) => (
              <div key={category.name}>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>{CATEGORY_LABEL[category.name]}</span>
                  <ScoreCounter
                    value={category.score}
                    minWidthCh={4}
                    className="font-mono tabular-nums"
                  />
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-border">
                  <div
                    className={cn("h-full rounded-full", category.score === null ? "bg-transparent" : band.bar)}
                    style={{ width: `${category.score ?? 0}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
