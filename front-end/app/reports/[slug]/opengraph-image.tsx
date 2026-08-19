import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ScoreSigil } from "@/components/brand/ScoreSigil";
import { getReportBySlug } from "@/lib/data/reports";
import { stripMarkdown } from "@/lib/text/markdown";

const BRAND_TICKER = "TECHINVES";
const BRAND_SCORE = 72;
const BRAND_SUBSCORES = [68, 80, 55, 74];

export const alt = "TechInves Score Report";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const frauncesSemiBold = await readFile(
  join(process.cwd(), "assets/fonts/Fraunces-SemiBold.woff"),
);
const sourceSansRegular = await readFile(
  join(process.cwd(), "assets/fonts/SourceSans3-Regular.woff"),
);
const sourceSansSemiBold = await readFile(
  join(process.cwd(), "assets/fonts/SourceSans3-SemiBold.woff"),
);

// Manual truncation rather than CSS line-clamp/text-overflow -- Satori's
// flexbox-only CSS subset doesn't reliably support either, so the dek is
// cut to a fixed character budget in JS before it ever reaches ImageResponse.
function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

export default async function Image({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const report = await getReportBySlug(slug);

  const runDate = report
    ? new Date(report.createdAt).toLocaleDateString("en-US", {
        day: "numeric",
        month: "long",
        year: "numeric",
      })
    : null;
  const headline = report ? report.title : "Report not found";
  const dek = report ? truncate(stripMarkdown(report.excerpt), 140) : null;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#ffffff",
          padding: "80px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <ScoreSigil
            ticker={BRAND_TICKER}
            score={BRAND_SCORE}
            subscores={BRAND_SUBSCORES}
            size="sm"
            tone="brand"
          />
          <span
            style={{
              display: "flex",
              fontFamily: "Source Sans 3",
              fontWeight: 600,
              fontSize: 26,
              color: "#525252",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
            }}
          >
            TechInves · Score Report
          </span>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          {runDate ? (
            <span
              style={{
                display: "flex",
                fontFamily: "Source Sans 3",
                fontWeight: 600,
                fontSize: 24,
                color: "#737373",
                marginBottom: 20,
              }}
            >
              {runDate}
            </span>
          ) : null}
          <span
            style={{
              display: "flex",
              fontFamily: "Fraunces",
              fontWeight: 600,
              fontSize: 58,
              lineHeight: 1.12,
              letterSpacing: "-0.02em",
              color: "#0a0a0a",
              maxWidth: 1000,
            }}
          >
            {headline}
          </span>
          {dek ? (
            <span
              style={{
                display: "flex",
                marginTop: 24,
                fontFamily: "Source Sans 3",
                fontWeight: 400,
                fontSize: 28,
                lineHeight: 1.45,
                color: "#525252",
                maxWidth: 900,
              }}
            >
              {dek}
            </span>
          ) : null}
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Fraunces", data: frauncesSemiBold, style: "normal", weight: 600 },
        { name: "Source Sans 3", data: sourceSansSemiBold, style: "normal", weight: 600 },
        { name: "Source Sans 3", data: sourceSansRegular, style: "normal", weight: 400 },
      ],
    },
  );
}
