import { Skeleton } from "@/components/ui/skeleton";

/**
 * Instant loading state for `/screener`. The route reads `getAllScores()`
 * server-side (`app/screener/page.tsx`), and `apiFetch` allows up to 8 s
 * before it gives up, so without this file a slow API left the visitor on
 * the previous page with no feedback at all for that whole window.
 *
 * Deliberately mirrors the real layout -- headline block, filter row, table
 * -- so the swap when the data lands is a fill, not a jump.
 */
export default function Loading() {
  return (
    <div>
      <section className="section-y">
        <div className="content-wrap">
          <div className="prose-measure">
            <Skeleton className="h-11 w-full sm:h-14" />
            <Skeleton className="mt-3 h-11 w-3/5 sm:h-14" />
            <Skeleton className="mt-6 h-5 w-full" />
            <Skeleton className="mt-2 h-5 w-4/5" />
          </div>

          <div className="mt-10">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <Skeleton className="h-8 w-full sm:max-w-xs" />
              <Skeleton className="h-8 w-full sm:w-64" />
            </div>
            <div className="mt-4 overflow-hidden rounded-lg bg-card ring-1 ring-foreground/10">
              <div className="border-b border-border px-4 py-3">
                <Skeleton className="h-4 w-40" />
              </div>
              {/* Ten rows: enough to fill a first viewport without
                  promising a specific row count the data may not have. */}
              {Array.from({ length: 10 }).map((_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-4 border-b border-border/60 px-4 py-3 last:border-b-0"
                >
                  <Skeleton className="h-4 w-16" />
                  <Skeleton className="h-4 flex-1" />
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-4 w-12" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
