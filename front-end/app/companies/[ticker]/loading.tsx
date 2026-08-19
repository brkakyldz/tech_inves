import { Skeleton } from "@/components/ui/skeleton";

/**
 * Instant loading state for a company detail page. This route is the one
 * that most needs it: it awaits two sequential API reads
 * (`getScoreByTicker` then `getScoreHistory`), and since
 * `generateStaticParams` now yields nothing when the API is unreachable at
 * build time, a ticker is frequently rendered on demand rather than served
 * from a prerender.
 *
 * The back link is real markup, not a placeholder -- it is static, it is
 * the one control a visitor may want *while* waiting, and rendering it as
 * a grey block would be a lie about what is loading.
 */
export default function Loading() {
  return (
    <div className="section-y content-wrap">
      <Skeleton className="h-5 w-40" />

      <div className="mt-6 flex flex-wrap items-start justify-between gap-6">
        <div className="prose-measure w-full max-w-xl">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="mt-3 h-11 w-4/5 sm:h-14" />
          <Skeleton className="mt-4 h-5 w-full" />
          <Skeleton className="mt-2 h-5 w-2/3" />
        </div>
        <div className="flex flex-col items-end gap-2">
          <Skeleton className="h-5 w-24 rounded-full" />
          <Skeleton className="h-5 w-28 rounded-full" />
        </div>
      </div>

      <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-3">
        <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10 sm:col-span-1">
          <div className="flex h-full flex-col items-center justify-center gap-3 py-10">
            <Skeleton className="h-12 w-28" />
            <Skeleton className="h-4 w-36" />
            <Skeleton className="h-4 w-24" />
          </div>
        </div>
        <div className="space-y-4 rounded-xl bg-card p-4 ring-1 ring-foreground/10 sm:col-span-2">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-56 w-full" />
        </div>
      </div>

      <div className="mt-6 space-y-4 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
        <Skeleton className="h-4 w-32" />
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-1.5">
            <div className="flex items-center justify-between gap-4">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-28" />
            </div>
            <Skeleton className="h-2 w-full rounded-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
