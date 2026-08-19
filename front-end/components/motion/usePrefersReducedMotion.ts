"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches;
}

// Server has no matchMedia; render as if motion is enabled so the SSR
// markup matches the client's *first* render (React resolves the two via
// useSyncExternalStore's server-snapshot mechanism, not a post-mount
// setState -- see https://react.dev/reference/react/useSyncExternalStore).
function getServerSnapshot(): boolean {
  return false;
}

/**
 * SSR-safe subscription to prefers-reduced-motion. The blanket CSS media
 * query guard in globals.css (@layer base) already handles pure-CSS
 * transitions/animations; this hook exists for the two effects (counter,
 * chart draw-on) that need a JS branch -- skipping the count-up loop and
 * the recharts animation entirely, not just running them faster.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
