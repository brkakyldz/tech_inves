/**
 * The "there is nothing here yet" panel used by the API-backed pages.
 *
 * It exists because `lib/data/scores.ts` and `lib/data/reports.ts` now
 * degrade an unreachable API to an empty collection (so `next build` works
 * on a fresh clone with no back-end running, ADR 0010 §7) -- which means
 * every page that lists data has a real, reachable empty state that a
 * visitor can land on, not just a theoretical one. The wording is the
 * page's job; this component only owns the shape.
 *
 * Quiet by design (plan §7.2): a dashed rule and muted copy, no icon, no
 * illustration -- an empty table is not an error and must not be dressed
 * up as one.
 */
export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
      <p className="font-serif text-lg font-semibold tracking-tight text-balance">{title}</p>
      {children ? (
        <div className="prose-measure mx-auto mt-3 text-sm text-muted-foreground text-pretty">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export default EmptyState;
