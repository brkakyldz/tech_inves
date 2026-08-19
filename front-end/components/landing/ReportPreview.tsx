import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { VerifierStatusChip } from "@/components/reports/VerifierBanner";
import { getLatestReport } from "@/lib/data/reports";
import { stripMarkdown } from "@/lib/text/markdown";

export async function ReportPreview() {
  const report = await getLatestReport();
  if (!report) return null;

  const runDate = new Date(report.createdAt).toLocaleDateString("en-US", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <section className="section-y bg-muted/30">
      <div className="content-wrap">
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_2fr]">
          <div>
            <h2 className="font-serif text-3xl font-semibold tracking-tight">
              Latest report
            </h2>
            <p className="mt-3 text-muted-foreground text-pretty">
              Each report carries the full score table, a cohort-level
              breakdown, and detailed analysis of the featured companies.
            </p>
          </div>

          <Link href={`/reports/${report.slug}`}>
            <Card className="transition-shadow hover:shadow-md">
              <CardContent className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Badge variant="outline">{runDate}</Badge>
                  {/*
                    ADR 0010 §6: the most prominent link to a report on the
                    whole site must not present a blocked one as the week's
                    finished output. Same model as the full banner
                    (lib/verifier/banner.ts), so the two cannot disagree.
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
                <h3 className="font-serif text-xl font-semibold text-balance">
                  {report.title}
                </h3>
                <p className="text-sm text-muted-foreground text-pretty">
                  {stripMarkdown(report.excerpt)}
                </p>
              </CardContent>
            </Card>
          </Link>
          <Link
            href="/reports"
            className="mt-3 inline-block text-sm font-medium underline underline-offset-4 hover:text-foreground"
          >
            Browse the full report archive &rarr;
          </Link>
        </div>
      </div>
    </section>
  );
}
