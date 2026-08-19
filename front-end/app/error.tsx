"use client";

/**
 * The root error boundary. Before this file existed the App Router shipped
 * none at all, so any throw from a server component -- and the API-backed
 * pages throw on purpose, see below -- dropped the visitor onto Next's
 * unstyled default error screen with the demo banner, the disclaimer and
 * the site chrome all gone.
 *
 * What actually reaches here: `lib/data/scores.ts` and `lib/data/reports.ts`
 * swallow *only* an unreachable API (`isApiUnreachable`, status 0) on their
 * collection reads, and only so `next build` works without a back-end. Every
 * real HTTP status, and every failure of the single-record reads
 * (`getScoreByTicker`, `getScoreHistory`, `getReportBySlug`), still throws --
 * that is deliberate, and this is the surface it was deliberate for. So the
 * copy names the two things a reader can actually check rather than
 * pretending to know which one it was.
 *
 * `retry` (stable in Next 16.3, the older `reset` only clears the boundary)
 * re-fetches and re-renders the segment, which is exactly the recovery an
 * intermittent API failure needs.
 */

import { useEffect } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    // No error-reporting service in a self-hosted personal deployment
    // (ADR 0010 §1) -- the server console is the log. In production the
    // server component message is redacted to a `digest`, which is what
    // matches this line up with the server-side entry.
    console.error(error);
  }, [error]);

  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
            Something went wrong
          </p>
          <h1 className="mt-4 font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            This page could not be rendered
          </h1>
          <p className="text-dek mt-6 text-muted-foreground text-pretty">
            The data behind this page could not be loaded. The usual cause is
            the back-end API being down, restarting, or answering with an
            error &mdash; the site itself is fine, and nothing was lost.
          </p>
          {error.digest ? (
            <p className="mt-4 text-sm text-muted-foreground">
              Server log reference:{" "}
              <span className="font-mono">{error.digest}</span>
            </p>
          ) : null}
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Button size="lg" onClick={() => retry()}>
              Try again
            </Button>
            <Button asChild variant="outline" size="lg">
              <Link href="/">Back to the control panel</Link>
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
