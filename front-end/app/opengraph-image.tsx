import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ScoreSigil } from "@/components/brand/ScoreSigil";

// Same fixed "signature" input components/brand/Wordmark.tsx uses -- the
// site-level OG mark represents the product, not any one company's score.
const BRAND_TICKER = "TECHINVES";
const BRAND_SCORE = 72;
const BRAND_SUBSCORES = [68, 80, 55, 74];

export const alt = "TechInves — US Technology Sector Score Report";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Subsetted single-weight WOFFs under assets/fonts (latin + latin-ext +
// digits + punctuation only -- see assets/fonts/README.md), not the full
// variable families next/font/google loads for the live site. Read once at
// module scope: the asset doesn't depend on request data (Next's own
// "Predictable values" guidance for opengraph-image local assets).
const frauncesSemiBold = await readFile(
  join(process.cwd(), "assets/fonts/Fraunces-SemiBold.woff"),
);
const sourceSansRegular = await readFile(
  join(process.cwd(), "assets/fonts/SourceSans3-Regular.woff"),
);

// Quiet and typographic per the design-system plan §7.2 (Stripe-press
// reference) -- no gradient, no decoration, one ornament (the sigil).
//
// tone="brand" -- ScoreSigil resolves this to a Satori-safe hex internally
// (see lib/sigil.ts's brandHueHex), so no per-call-site workaround is
// needed here.
export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          background: "#ffffff",
          padding: "96px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
          <ScoreSigil
            ticker={BRAND_TICKER}
            score={BRAND_SCORE}
            subscores={BRAND_SUBSCORES}
            size="lg"
            tone="brand"
          />
          <span
            style={{
              display: "flex",
              fontFamily: "Fraunces",
              fontWeight: 600,
              fontSize: 96,
              color: "#0a0a0a",
              letterSpacing: "-0.02em",
            }}
          >
            TechInves
          </span>
        </div>
        <div
          style={{
            display: "flex",
            marginTop: 44,
            fontFamily: "Source Sans 3",
            fontWeight: 400,
            fontSize: 34,
            lineHeight: 1.4,
            color: "#525252",
            maxWidth: 880,
          }}
        >
          Deterministic financial scoring and sector reporting for US
          technology companies.
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Fraunces", data: frauncesSemiBold, style: "normal", weight: 600 },
        { name: "Source Sans 3", data: sourceSansRegular, style: "normal", weight: 400 },
      ],
    },
  );
}
