"use client";

import { useEffect, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";
import type { CategoryScore } from "@/lib/data/types";
import { CATEGORY_LABEL } from "@/lib/labels";
import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";

// Mirrors --duration-entrance (320ms) in globals.css -- see
// ScoreHistoryChart.tsx for why this is a duplicated numeric literal.
const CHART_ANIMATION_DURATION = 320;

export function CategoryRadarChart({
  categories,
}: {
  categories: CategoryScore[];
}) {
  // Chart draw-on (motion effect b), scoped to initial mount only -- see
  // ScoreHistoryChart.tsx for the same pattern and rationale.
  const [hasAnimated, setHasAnimated] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    if (hasAnimated) return;
    const timer = setTimeout(() => setHasAnimated(true), CHART_ANIMATION_DURATION + 50);
    return () => clearTimeout(timer);
  }, [hasAnimated]);

  // Categories with no computable score ("no data") are omitted from the
  // radar rather than plotted as 0 -- 0 would render as "measured, worst
  // possible", which is a different, false claim.
  const data = categories
    .filter((c) => c.score !== null)
    .map((c) => ({
      category: CATEGORY_LABEL[c.name],
      score: c.score,
    }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <RadarChart data={data} outerRadius="68%">
        <PolarGrid stroke="var(--border)" strokeOpacity={0.5} />
        <PolarAngleAxis
          dataKey="category"
          tickLine={false}
          axisLine={false}
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
        />
        <Radar
          dataKey="score"
          stroke="var(--chart-5)"
          fill="var(--chart-5)"
          fillOpacity={0.2}
          strokeDasharray="6 3"
          isAnimationActive={!hasAnimated && !reducedMotion}
          animationDuration={CHART_ANIMATION_DURATION}
          animationEasing="ease-out"
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}
