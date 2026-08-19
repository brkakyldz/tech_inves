import { Skeleton } from "@/components/ui/skeleton";

/**
 * Instant loading state for the report archive, and -- since it sits at the
 * `reports` segment -- for `/reports/[slug]` beneath it too, which is the
 * heavier of the two reads (the full report body, not a list of excerpts).
 * A headline block over stacked cards is the shape both routes share.
 */
export default function Loading() {
  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <Skeleton className="h-11 w-full sm:h-14" />
          <Skeleton className="mt-3 h-11 w-2/3 sm:h-14" />
          <Skeleton className="mt-6 h-5 w-full" />
          <Skeleton className="mt-2 h-5 w-3/4" />
        </div>

        <div className="mt-10 space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="space-y-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Skeleton className="h-5 w-32 rounded-full" />
                <Skeleton className="h-5 w-24 rounded-full" />
                <Skeleton className="h-5 w-14 rounded-full" />
              </div>
              <Skeleton className="h-6 w-3/4" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
