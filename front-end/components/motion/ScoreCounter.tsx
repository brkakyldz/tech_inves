"use client";

import { useEffect, useLayoutEffect, useRef } from "react";
import { registerCounterFrame } from "./counterDriver";
import { usePrefersReducedMotion } from "./usePrefersReducedMotion";
import { formatScore } from "@/lib/scoreColor";

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

// useLayoutEffect warns when it runs during SSR (no DOM to lay out against).
// This component is always rendered by a Server Component, so the module
// itself does execute on the server -- but React only *calls* effects
// (useEffect or useLayoutEffect) during the client render/commit phase,
// never during server rendering. The `typeof window` guard below is the
// standard belt-and-suspenders for this: it keeps the hook identity stable
// (same hook every render, required by the Rules of Hooks) while making the
// SSR pass a no-op, so the warning genuinely cannot fire here. Falling back
// to useEffect on the server branch is purely to satisfy that check; it
// never actually runs there.
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

interface ScoreCounterProps {
  /** null renders the absent state and never counts -- see lib/scoreColor.ts
   * formatScore's contract: a missing metric is "N/A", never a fake 0. */
  value: number | null;
  /** Space reserved with `min-width` in `ch`, sized for the widest possible
   * rendered string (e.g. "100.0" = 5ch), so the box never reflows as the
   * digits change mid-count. Never use `width` -- that clips a longer
   * intermediate string instead of reserving room for it. */
  minWidthCh: number;
  className?: string;
  durationMs?: number;
}

/**
 * Score count-up (motion effect a, reports/research/2026-08-17_fe-motion-system.md §2).
 *
 * Renders two spans: an `aria-hidden` one that a shared rAF driver
 * (counterDriver.ts) writes to directly via ref (no per-frame React state,
 * no per-frame re-render), and an adjacent visually-hidden (`sr-only`) span
 * with the *final* value only, announced once. A 60Hz live region is
 * explicitly rejected by the research as unusable with a screen reader.
 *
 * Formatting is fixed to lib/scoreColor.ts's `formatScore` (not a function
 * prop): every caller here is a Server Component, and a plain function
 * cannot be passed as a prop across the Server->Client Component boundary
 * (it isn't serializable) -- this component imports it directly instead,
 * which works from both Server and Client callers.
 */
export function ScoreCounter({ value, minWidthCh, className, durationMs = 600 }: ScoreCounterProps) {
  const spanRef = useRef<HTMLSpanElement>(null);
  const reducedMotion = usePrefersReducedMotion();

  useIsomorphicLayoutEffect(() => {
    if (value === null) return;
    const el = spanRef.current;
    if (!el) return;

    if (reducedMotion) {
      // Reduced motion: the server-rendered markup already shows the final
      // value -- nothing to animate, nothing to reset.
      el.textContent = formatScore(value);
      return;
    }

    // Only now -- confirmed client-side, JS running, motion allowed -- do
    // we drop to 0 and animate up. This runs in a layout effect (before the
    // browser paints the first client frame) specifically so the reset to 0
    // is never itself painted: the user must never see final -> 0 -> final,
    // only 0 -> final. The server-rendered/first-paint HTML is always the
    // real final value (never a fake 0); this is a pure enhancement layered
    // on top before paint, never a substitute for the SSR value.
    let startTime: number | null = null;
    el.textContent = formatScore(0);
    const unregister = registerCounterFrame((now) => {
      if (startTime === null) startTime = now;
      const t = Math.min(1, (now - startTime) / durationMs);
      el.textContent = formatScore(value * easeOutCubic(t));
      if (t >= 1) unregister();
    });
    return unregister;
  }, [value, reducedMotion, durationMs]);

  const style = { minWidth: `${minWidthCh}ch` };

  if (value === null) {
    return (
      <span className={className} style={style}>
        {formatScore(null)}
      </span>
    );
  }

  return (
    <span className={className} style={{ ...style, display: "inline-block" }}>
      {/* Server-rendered (and first client paint) value is always the real,
          final value -- never a fake 0. Only once mounted, with JS running
          and motion allowed, does the effect above drop this to 0 and count
          back up; a client with no JS, a rAF that never fires, or a crawler
          that never runs effects still shows the correct number. */}
      <span ref={spanRef} aria-hidden="true">
        {formatScore(value)}
      </span>
      <span className="sr-only">{formatScore(value)}</span>
    </span>
  );
}
