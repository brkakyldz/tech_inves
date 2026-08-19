// Explicit, like components/reports/VerifierBanner.tsx: `npm test` renders
// these components through jiti's classic JSX runtime, which resolves `React`
// as a free variable rather than auto-importing it.
import * as React from "react";

import { deltaUnavailableLabel } from "@/lib/labels";
import type { ScoreDelta } from "@/lib/data/types";
import { cn } from "@/lib/utils";

interface DeltaIndicatorProps {
  /** Signed change, e.g. this period's composite score minus the previous
   * period's. Positive = up, negative = down, 0 = unchanged. */
  delta: number;
  /** Formats the absolute magnitude for display (no sign -- the component
   * adds its own +/- and arrow). Defaults to one decimal place. */
  format?: (absDelta: number) => string;
  className?: string;
}

/**
 * Week/period-over-period delta emphasis (motion effect d). Server
 * component -- no client JS needed: the pulse is a CSS animation that runs
 * once on mount, using only compositor-only properties (transform/opacity),
 * and the blanket `prefers-reduced-motion: reduce` guard already in
 * globals.css (`@layer base`) collapses `animation-duration` to ~0ms and
 * `animation-iteration-count` to 1, which satisfies "keep the arrow and
 * color, drop the pulse" without any extra branching here.
 *
 * Hue is never the only channel (WCAG 1.4.1): the arrow glyph carries
 * direction independently of --delta-up/--delta-down.
 */
export function DeltaIndicator({ delta, format, className }: DeltaIndicatorProps) {
  const fmt = format ?? ((v: number) => v.toFixed(1));
  const magnitude = fmt(Math.abs(delta));
  // Decide direction from what's actually displayed, not the raw value: a
  // delta that rounds to "0.0" at display precision (e.g. 0.02) must render
  // as unchanged, not as a signed arrow asserting a direction the printed
  // number doesn't support. Number(magnitude) === 0 catches "0.0", "0.00",
  // etc. regardless of the format function's precision.
  const displaysAsZero = Number(magnitude) === 0;

  if (delta === 0 || displaysAsZero) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 font-mono tabular-nums text-muted-foreground",
          className,
        )}
      >
        <span aria-hidden="true">&rarr;</span>
        <span>{magnitude}</span>
        <span className="sr-only">no change</span>
      </span>
    );
  }

  const isUp = delta > 0;

  return (
    <span
      className={cn(
        "delta-pulse inline-flex items-center gap-1 font-mono tabular-nums",
        isUp ? "text-[var(--delta-up)]" : "text-[var(--delta-down)]",
        className,
      )}
    >
      <span aria-hidden="true">{isUp ? "▲" : "▼"}</span>
      <span>
        {isUp ? "+" : "-"}
        {magnitude}
      </span>
      <span className="sr-only">{isUp ? "increased" : "decreased"}</span>
    </span>
  );
}

/**
 * The state where there *is* no delta (plan §8 Faz 7a).
 *
 * Two runs are not always comparable -- a run that did not finish, one whose
 * inputs cannot be accounted for, a cohort or regime change that moved the
 * basis the composite is computed on -- and the very first run has nothing
 * behind it at all. The API answers those with a reason instead of a number
 * (`ScoreDelta.unavailableReason`), and this renders the reason.
 *
 * It exists so that no caller is ever tempted to pass `0` in place of a
 * missing delta. A zero delta is a *measurement* -- "unchanged" -- and
 * `DeltaIndicator` renders it with a neutral arrow and no direction. This is
 * a different statement: the comparison was declined. Collapsing the two
 * would put a confident number on a pair the data does not support, which is
 * precisely what ADR 0009's data-boundary caution is about.
 */
export function DeltaUnavailable({
  reason,
  className,
}: {
  reason: string | null;
  className?: string;
}) {
  const label = deltaUnavailableLabel(reason);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-muted-foreground",
        className,
      )}
    >
      {/* Carries no direction, because there is none to carry. */}
      <span aria-hidden="true">&mdash;</span>
      <span>{label}</span>
    </span>
  );
}

/**
 * The one place the null branch is taken, so no caller has to remember it.
 * `delta === null` and `delta === 0` are different states and this is what
 * keeps them apart at every call site.
 *
 * The prop admits `null | undefined` on purpose, even though the API always
 * sends the field. This payload arrives over HTTP through a tag-based fetch
 * cache with an hour-long fallback TTL (`lib/api/client.ts`), so a response
 * cached by a build that predates this field is a reachable runtime state --
 * it is what a stale dev cache produced the first time this component was
 * driven in a browser. A missing field degrades to the unavailable label,
 * which is the honest reading of it: nothing is known about the change. It
 * must never degrade to a crash, and it must never degrade to a number.
 */
export function ScoreDeltaDisplay({
  delta,
  format,
  className,
  unavailableClassName,
}: {
  delta: ScoreDelta | null | undefined;
  format?: (absDelta: number) => string;
  className?: string;
  unavailableClassName?: string;
}) {
  if (!delta) {
    return (
      <DeltaUnavailable reason={null} className={unavailableClassName ?? className} />
    );
  }
  if (delta.delta === null || delta.delta === undefined) {
    return (
      <DeltaUnavailable
        reason={delta.unavailableReason ?? null}
        className={unavailableClassName ?? className}
      />
    );
  }
  return <DeltaIndicator delta={delta.delta} format={format} className={className} />;
}
