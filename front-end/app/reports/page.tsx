import type { Metadata } from "next";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/layout/EmptyState";
import { VerifierStatusChip } from "@/components/reports/VerifierBanner";
import { getAllReports } from "@/lib/data/reports";
import { stripMarkdown } from "@/lib/text/markdown";

export const metadata: Metadata = {
  title: "Report Archive — TechInves",
  description: "The full list of past reports.",
};

export default async function ReportsArchivePage() {
  const reports = await getAllReports();
  const latest = reports[0];

  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <h1 className="font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            {latest
              ? `${reports.length} reports, one archive`
              : "Published reports collect here"}
          </h1>
          <p className="text-dek mt-4 text-muted-foreground text-pretty">
            {latest
              ? `Most recently published ${new Date(latest.createdAt).toLocaleDateString("en-US", { day: "numeric", month: "long", year: "numeric" })}. Every published report, newest to oldest.`
              : "Every published report, newest to oldest."}
          </p>
        </div>

        <div className="mt-10 space-y-4">
          {/* Same reachable-empty-state rule as the screener: with no API
              running `getAllReports()` returns [] instead of throwing, so
              the archive states why it is empty rather than rendering a
              bare heading over nothing. */}
          {reports.length === 0 && (
            <EmptyState title="No reports published yet">
              Either no report run has finished yet, or the back-end API is not
              reachable from this site. Start the API, then run{" "}
              <span className="font-medium">Generate report</span> from the{" "}
              <Link href="/" className="underline underline-offset-4">
                control panel
              </Link>
              .
            </EmptyState>
          )}
          {reports.map((report) => {
            const runDate = new Date(report.createdAt).toLocaleDateString("en-US", {
              day: "numeric",
              month: "long",
              year: "numeric",
            });
            return (
              <Link key={report.slug} href={`/reports/${report.slug}`}>
                <Card className="transition-colors hover:bg-muted/40">
                  <CardContent className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{runDate}</Badge>
                      {/*
                        ADR 0010 §6: an unsound report is marked as unsound at
                        the point the reader decides whether to open it, not
                        only after they already have. Same model as the full
                        banner (lib/verifier/banner.ts), so the archive and
                        the report itself cannot disagree.
                      */}
                      <VerifierStatusChip
                        verdict={report.verifierVerdict}
                        violations={null}
                        isPartial={report.isPartial}
                      />
                      {report.highlightedTickers.map((ticker) => (
                        <Badge key={ticker} variant="secondary" className="font-mono">
                          {ticker}
                        </Badge>
                      ))}
                    </div>
                    <h2 className="font-serif text-lg font-semibold text-balance">
                      {report.title}
                    </h2>
                    <p className="text-sm text-muted-foreground text-pretty">
                      {stripMarkdown(report.excerpt)}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}
