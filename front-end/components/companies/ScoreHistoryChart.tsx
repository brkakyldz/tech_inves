"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ScoreHistoryPoint } from "@/lib/data/types";
import { formatScore } from "@/lib/scoreColor";
import { usePrefersReducedMotion } from "@/components/motion/usePrefersReducedMotion";

// Mirrors --duration-entrance (320ms) in globals.css. recharts' animation
// props take a plain number, not a CSS var, so the value is duplicated here
// -- see reports/research/2026-08-17_fe-motion-system.md §3.
const CHART_ANIMATION_DURATION = 320;

export function ScoreHistoryChart({ points }: { points: ScoreHistoryPoint[] }) {
  // Chart line draw-on (motion effect b): recharts' own isAnimationActive,
  // scoped to the initial mount only via this ref-backed flag -- otherwise
  // every tooltip hover (which re-renders LineChart) replays the draw-on.
  const [hasAnimated, setHasAnimated] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    if (hasAnimated) return;
    const timer = setTimeout(() => setHasAnimated(true), CHART_ANIMATION_DURATION + 50);
    return () => clearTimeout(timer);
  }, [hasAnimated]);

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={points} margin={{ left: -12, right: 16, top: 12, bottom: 4 }}>
        <CartesianGrid
          stroke="var(--border)"
          strokeOpacity={0.5}
          strokeDasharray="4 4"
          vertical={false}
        />
        <XAxis
          dataKey="period"
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          tickMargin={10}
        />
        <YAxis
          domain={[0, 100]}
          tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          tickMargin={8}
          width={36}
        />
        <Tooltip
          contentStyle={{
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          formatter={(value) => formatScore(Number(value))}
        />
        <Line
          type="monotone"
          dataKey="compositeScore"
          stroke="var(--chart-5)"
          strokeWidth={2}
          dot={{ r: 3, fill: "var(--chart-5)", strokeWidth: 0 }}
          activeDot={{ r: 4 }}
          isAnimationActive={!hasAnimated && !reducedMotion}
          animationDuration={CHART_ANIMATION_DURATION}
          animationEasing="ease-out"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
