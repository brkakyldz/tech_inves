import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ScoreSigil } from "@/components/brand/ScoreSigil";
import { getScoreByTicker } from "@/lib/data/scores";
import { formatScore } from "@/lib/scoreColor";

export const alt = "TechInves Company Score Card";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const frauncesSemiBold = await readFile(
  join(process.cwd(), "assets/fonts/Fraunces-SemiBold.woff"),
);
const sourceSansRegular = await readFile(
  join(process.cwd(), "assets/fonts/SourceSans3-Regular.woff"),
);
const monoSemiBold = await readFile(
  join(process.cwd(), "assets/fonts/JetBrainsMono-SemiBold.woff"),
);

export default async function Image({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker: rawTicker } = await params;
  const ticker = rawTicker.toUpperCase();
  const score = await getScoreByTicker(ticker);

  // Absent state -- company not found, or the composite score is genuinely
  // unavailable -- is a distinct null, never a fake 0. Mirrors
  // lib/scoreColor.ts's formatScore ("N/A") and lib/sigil.ts's
  // ringAbsent/absent convention, the same rule this codebase applies
  // everywhere else a score renders.
  const compositeScore: number | null = score ? score.compositeScore : null;
  const companyName = score ? score.companyName : "Company not found";
  const subscores: (number | null)[] = score
    ? score.categories.map((c) => c.score)
    : [];

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "#ffffff",
          padding: "96px",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", maxWidth: 700 }}>
          <span
            style={{
              display: "flex",
              fontFamily: "JetBrains Mono",
              fontWeight: 600,
              fontSize: 30,
              color: "#525252",
              letterSpacing: "0.02em",
            }}
          >
            {ticker}
          </span>
          <span
            style={{
              display: "flex",
              marginTop: 16,
              fontFamily: "Fraunces",
              fontWeight: 600,
              fontSize: 60,
              lineHeight: 1.1,
              letterSpacing: "-0.02em",
              color: "#0a0a0a",
            }}
          >
            {companyName}
          </span>
          <span style={{ display: "flex", marginTop: 32, alignItems: "baseline", gap: 12 }}>
            {/* Numerals set in the mono face -- proportional-figure fonts
                misalign a score numeral, and the score is a number, not
                prose (design-system plan §6 Phase 6 task list). */}
            <span
              style={{
                display: "flex",
                fontFamily: "JetBrains Mono",
                fontWeight: 600,
                fontSize: 96,
                color: "#0a0a0a",
              }}
            >
              {formatScore(compositeScore)}
            </span>
            <span
              style={{
                display: "flex",
                fontFamily: "Source Sans 3",
                fontWeight: 400,
                fontSize: 28,
                color: "#737373",
              }}
            >
              / 100 composite score
            </span>
          </span>
        </div>

        <div style={{ display: "flex" }}>
          <ScoreSigil
            ticker={ticker}
            score={compositeScore}
            subscores={subscores}
            size="lg"
            tone="brand"
          />
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Fraunces", data: frauncesSemiBold, style: "normal", weight: 600 },
        { name: "Source Sans 3", data: sourceSansRegular, style: "normal", weight: 400 },
        { name: "JetBrains Mono", data: monoSemiBold, style: "normal", weight: 600 },
      ],
    },
  );
}
