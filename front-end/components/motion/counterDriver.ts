/**
 * One shared requestAnimationFrame driver for every active score counter on
 * the page, instead of each <ScoreCounter> running its own rAF loop.
 * See reports/research/2026-08-17_fe-motion-system.md §7 (Performance) --
 * ~40 independent rAF callbacks compete for scheduler slots; one loop that
 * fans out to N listeners does not.
 *
 * Not React state: listeners are plain callbacks invoked with the current
 * frame timestamp. Callers (useCountUp) write directly to a DOM ref inside
 * the callback rather than calling setState, so a running counter never
 * triggers a React re-render per frame.
 */

type FrameListener = (now: number) => void;

const listeners = new Set<FrameListener>();
let rafId: number | null = null;

function tick(now: number) {
  // Snapshot before iterating: a listener may unregister itself (the
  // common case, on reaching t >= 1) during this pass.
  for (const listener of Array.from(listeners)) {
    listener(now);
  }
  if (listeners.size > 0) {
    rafId = requestAnimationFrame(tick);
  } else {
    rafId = null;
  }
}

/** Registers a per-frame callback. Returns an unregister function. */
export function registerCounterFrame(listener: FrameListener): () => void {
  listeners.add(listener);
  if (rafId === null) {
    rafId = requestAnimationFrame(tick);
  }
  return () => {
    listeners.delete(listener);
  };
}
