import { ImageResponse } from "next/og";

import { ScoreSigil } from "@/components/brand/ScoreSigil";

// The site's own fixed "signature" input -- same constants
// components/brand/Wordmark.tsx uses. The favicon is the brand mark, not
// any one company's score card, so it never reads real ticker data.
const BRAND_TICKER = "TECHINVES";
const BRAND_SCORE = 72;
const BRAND_SUBSCORES = [68, 80, 55, 74];

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

// "sm" is the small-size drawing (components/brand/README.md) -- it drops
// the fine baseline ring/center dot and thickens every stroke so the mark
// stays a legible silhouette at 32px instead of turning to mud. Its native
// px matches the icon size exactly, so no scaling is involved.
//
// tone="brand" -- ScoreSigil resolves this to a Satori-safe hex internally
// (see lib/sigil.ts's brandHueHex), so no per-call-site workaround is
// needed here.
export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#ffffff",
        }}
      >
        <ScoreSigil
          ticker={BRAND_TICKER}
          score={BRAND_SCORE}
          subscores={BRAND_SUBSCORES}
          size="sm"
          tone="brand"
        />
      </div>
    ),
    { ...size },
  );
}
