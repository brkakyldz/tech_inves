import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatCoveragePct, LOW_RELIABILITY_COVERAGE_THRESHOLD } from "@/lib/scoreColor";

/**
 * R27: shown next to the composite score wherever it appears -- a
 * 55%-coverage 84 and a 98%-coverage 84 previously looked identical
 * everywhere except Coverage Notes prose.
 */
export function CoverageBadge({ coveragePct }: { coveragePct: number }) {
  const low = coveragePct < LOW_RELIABILITY_COVERAGE_THRESHOLD;
  return (
    <Badge
      variant="outline"
      className={cn(
        "shrink-0 text-[10px] font-normal",
        low
          ? "border-[var(--score-moderate-border)] text-[var(--score-moderate)]"
          : "border-border text-muted-foreground",
      )}
      title={low ? "Low reliability: data coverage below 60%" : "Data coverage"}
    >
      <span className="font-mono tabular-nums">{formatCoveragePct(coveragePct)}</span> coverage
    </Badge>
  );
}
