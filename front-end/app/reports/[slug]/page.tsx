import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/badge";
import { VerifierBanner } from "@/components/reports/VerifierBanner";
import { getAllReports, getReportBySlug } from "@/lib/data/reports";
import type { ReportSection } from "@/lib/data/types";
import { newsreader } from "@/app/fonts";
import { cn } from "@/lib/utils";
import { stripMarkdown } from "@/lib/text/markdown";

// Long-form report prose only (plan §2/§6 Phase 4): body text (paragraphs,
// list items) reads in Newsreader, capped at --prose-measure (~65ch).
// Headings stay in font-serif (Fraunces) so the display-headline voice is
// consistent with every other page on the site -- only the reading copy
// switches typeface here. Newsreader is applied via an arbitrary CSS
// property, not the global `font-serif`/`font-sans` utilities, so it never
// leaks onto any other route. Tables are deliberately NOT given the
// `prose-measure` class -- `max-width` is not an inherited CSS property, so
// simply never setting it on the table wrapper is sufficient to keep it
// unconstrained.
const PROSE_TEXT_CLASS = "prose-measure [font-family:var(--font-newsreader)]";

const MARKDOWN_COMPONENTS = {
  h1: (props: React.ComponentPropsWithoutRef<"h1">) => (
    <h1 className="mt-8 font-serif text-2xl font-semibold tracking-tight text-balance" {...props} />
  ),
  h2: (props: React.ComponentPropsWithoutRef<"h2">) => (
    <h2 className="mt-6 font-serif text-xl font-semibold tracking-tight text-balance" {...props} />
  ),
  h3: (props: React.ComponentPropsWithoutRef<"h3">) => (
    <h3 className="mt-4 font-serif text-lg font-semibold text-balance" {...props} />
  ),
  p: (props: React.ComponentPropsWithoutRef<"p">) => (
    <p className={cn("mt-3 text-muted-foreground text-pretty leading-relaxed", PROSE_TEXT_CLASS)} {...props} />
  ),
  a: (props: React.ComponentPropsWithoutRef<"a">) => (
    <a className="underline underline-offset-4 hover:text-foreground" {...props} />
  ),
  li: (props: React.ComponentPropsWithoutRef<"li">) => (
    <li className={cn("mt-1 text-muted-foreground", PROSE_TEXT_CLASS)} {...props} />
  ),
  table: (props: React.ComponentPropsWithoutRef<"table">) => (
    <div className="mt-4 max-w-none overflow-x-auto">
      <table className="w-full border-collapse text-sm" {...props} />
    </div>
  ),
  th: (props: React.ComponentPropsWithoutRef<"th">) => (
    <th className="border-b border-border px-2 py-1.5 text-left font-medium" {...props} />
  ),
  td: (props: React.ComponentPropsWithoutRef<"td">) => (
    <td className="border-b border-border px-2 py-1.5" {...props} />
  ),
  code: (props: React.ComponentPropsWithoutRef<"code">) => (
    <code className="rounded bg-muted px-1 py-0.5 text-xs" {...props} />
  ),
  pre: (props: React.ComponentPropsWithoutRef<"pre">) => (
    <pre className="mt-3 max-w-none overflow-x-auto rounded-md bg-muted p-3 text-xs" {...props} />
  ),
};

function ReportSectionBlock({ section }: { section: ReportSection }) {
  // section.bodyMarkdown already includes its own ##/### heading line (see
  // pipeline/storage/report_store.py's split_into_sections()), so it's
  // rendered as-is; the ticker badge/link is supplementary, not a
  // duplicate title. Sections separate by space (plan §7.2), not a
  // border-t rule.
  return (
    <section className="mt-10 first:mt-0">
      {section.ticker && (
        <Link href={`/companies/${section.ticker}`} className="inline-block">
          <Badge variant="secondary" className="font-mono hover:bg-secondary/70">
            {section.ticker}
          </Badge>
        </Link>
      )}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {section.bodyMarkdown}
      </ReactMarkdown>
    </section>
  );
}

// Explicit for the same reason as app/companies/[ticker]/page.tsx: with no
// API reachable at build time `getAllReports()` returns [], nothing is
// prerendered, and every report renders on demand once the API is up.
export const dynamicParams = true;

export async function generateStaticParams() {
  const reports = await getAllReports();
  return reports.map((r) => ({ slug: r.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  let report: Awaited<ReturnType<typeof getReportBySlug>>;
  try {
    report = await getReportBySlug(slug);
  } catch {
    // Same reasoning as app/companies/[ticker]/page.tsx: a throw in
    // `generateMetadata` bypasses the segment's error boundary entirely and
    // renders a bare "Internal Server Error" with no document. The page
    // body still throws, so `app/error.tsx` is what the reader sees.
    return { title: "TechInves" };
  }
  if (!report) return { title: "Report not found — TechInves" };
  return {
    title: `${report.title} — TechInves`,
    description: stripMarkdown(report.excerpt),
  };
}

export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const report = await getReportBySlug(slug);
  if (!report) notFound();

  const runDate = new Date(report.createdAt).toLocaleDateString("en-US", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  // "overview" is the lead content before the writer's first heading (title
  // line + opening disclaimer) -- already covered by the h1/excerpt above,
  // so it's excluded here to avoid rendering it twice.
  const sections = [...(report.sections ?? [])]
    .filter((s) => s.topic !== "overview")
    .sort((a, b) => a.orderIndex - b.orderIndex);

  return (
    <div className={cn(newsreader.variable, "section-y content-wrap")}>
      {/*
        ADR 0010 §6: first element in the document, above the report's own
        title and above every section. Position is the requirement, not a
        layout preference -- a warning a reader meets after the content has
        already started is a warning that arrives too late to change how the
        content is read. It is placed here in source order rather than moved
        into place with CSS, so it stays above the report with stylesheets
        disabled. Renders nothing at all only for a clean verdict on a full
        watchlist run (see lib/verifier/banner.ts).
      */}
      <VerifierBanner
        verdict={report.verifierVerdict}
        violations={report.verifierViolations}
        isPartial={report.isPartial}
      />

      <Link href="/reports" className="text-sm text-muted-foreground hover:text-foreground">
        &larr; Back to the report archive
      </Link>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Badge variant="outline">{runDate}</Badge>
        {report.highlightedTickers.map((ticker) => (
          <Link key={ticker} href={`/companies/${ticker}`}>
            <Badge variant="secondary" className="font-mono hover:bg-secondary/70">
              {ticker}
            </Badge>
          </Link>
        ))}
      </div>

      <h1 className="mt-4 font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
        {report.title}
      </h1>

      <p className={cn("mt-6 text-muted-foreground text-pretty leading-relaxed", PROSE_TEXT_CLASS)}>
        {stripMarkdown(report.excerpt)}
      </p>

      {sections.length > 0 ? (
        <div className="mt-2">
          {sections.map((section) => (
            <ReportSectionBlock
              key={`${section.sectionType}-${section.ticker ?? section.topic ?? section.title}`}
              section={section}
            />
          ))}
        </div>
      ) : null}

      <p className="mt-10 text-xs text-muted-foreground text-pretty">
        This report is a screening/analysis summary, not investment advice.
        For details, see the{" "}
        <Link href="/legal" className="underline">
          methodology page
        </Link>
        .
      </p>
    </div>
  );
}
