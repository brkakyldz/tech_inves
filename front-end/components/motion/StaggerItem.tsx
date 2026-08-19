"use client";

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { cn } from "@/lib/utils";

// 40 cards x 40ms (--stagger-step) would be 1.6s before the last one
// appears, which reads as broken -- cap the effective index (research §7).
const MAX_STAGGER_INDEX = 12;

interface StaggerItemProps {
  index: number;
  children: ReactNode;
  className?: string;
}

/**
 * Staggered card entrance (motion effect c). The pre-animation state
 * (opacity: 0, translateY) is the plain `.stagger-item` CSS class applied
 * unconditionally -- the *server-rendered* default, per the research's
 * SSR-safety requirement: a slow hydration must never show a flash of
 * already-visible cards snapping into the pre-animation state. This client
 * component only *adds* `.is-visible` once IntersectionObserver fires; it
 * never removes or overrides server-set styles.
 *
 * Unobserves after the first fire, so scrolling the grid out of view and
 * back never re-triggers the entrance.
 */
export function StaggerItem({ index, children, className }: StaggerItemProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Safety net: no IntersectionObserver support means the reveal trigger
    // can never fire, so content would otherwise be stranded hidden forever
    // (under the .js scope in globals.css). Reveal immediately instead.
    if (typeof IntersectionObserver === "undefined") {
      el.classList.add("is-visible");
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.15 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={cn("stagger-item", className)}
      style={{ "--i": Math.min(index, MAX_STAGGER_INDEX) } as CSSProperties}
    >
      {children}
    </div>
  );
}
