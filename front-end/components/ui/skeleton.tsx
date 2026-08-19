import { cn } from "@/lib/utils"

// Placeholder block for the route-level `loading.tsx` files. Same shape as
// the other primitives in this directory (data-slot + cn passthrough) so it
// composes the same way.
//
// `animate-pulse` needs no reduced-motion branch: the blanket guard in
// app/globals.css already collapses animation-duration for
// prefers-reduced-motion, and Tailwind's pulse keyframes end at opacity 1,
// so the block simply renders static.
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  )
}

export { Skeleton }
