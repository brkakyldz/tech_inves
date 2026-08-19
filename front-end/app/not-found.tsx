import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/ui/button";

/**
 * The root 404. It covers both callers: an explicit `notFound()` -- the
 * company detail page raises one for a ticker the API does not know
 * (`app/companies/[ticker]/page.tsx`), the report page for an unknown slug
 * -- and any URL that matches no route at all (Next 13.3+ routes unmatched
 * URLs to the root `not-found`). Both previously fell through to the
 * framework default, which renders outside this site's chrome entirely.
 *
 * Note what this page must NOT say: "this company does not exist" is a
 * claim about the data, and the fetchers are built so that only a real 404
 * from a live API can produce it (an unreachable API throws instead and
 * lands on `app/error.tsx`). So the copy can state the absence plainly.
 */
export const metadata: Metadata = {
  title: "Not found — TechInves",
  description: "This page does not exist.",
};

export default function NotFound() {
  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <p className="font-mono text-sm text-muted-foreground">404</p>
          <h1 className="mt-4 font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            There is nothing at this address
          </h1>
          <p className="text-dek mt-6 text-muted-foreground text-pretty">
            The page you asked for does not exist. If you followed a link to a
            company or a report, it may be a ticker outside the watchlist or a
            report that was never published.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Button asChild size="lg">
              <Link href="/screener">Browse all scores</Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link href="/reports">Report archive</Link>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
