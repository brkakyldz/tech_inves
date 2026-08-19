import { ImageResponse } from "next/og";

import { ScoreSigil } from "@/components/brand/ScoreSigil";

const BRAND_TICKER = "TECHINVES";
const BRAND_SCORE = 72;
const BRAND_SUBSCORES = [68, 80, 55, 74];

// Next places no enumerated size matrix on `icon`/`apple-icon` -- `size` is
// an arbitrary {width, height} pair (verified directly against
// node_modules/next/dist/docs/.../app-icons.md, which documents only the
// `size`/`contentType` config exports, no fixed dimension list). Apple's own
// platform convention for the classic non-maskable touch icon is 180x180,
// but components/brand/ScoreSigil.tsx's "lg" drawing has a fixed 200px
// native size (not overridable via props without touching that file, which
// is out of scope here) -- 200x200 avoids stretching/scaling the sigil off
// its native raster and still renders as a normal, non-maskable apple-touch
// icon.
export const size = { width: 200, height: 200 };
export const contentType = "image/png";

// "lg" is the hero/OG-scale drawing (fine baseline ring + center dot) --
// appropriate here since 200px comfortably clears the detail threshold that
// only the "sm" drawing exists to protect against (components/brand/README.md).
//
// tone="brand" -- ScoreSigil resolves this to a Satori-safe hex internally
// (see lib/sigil.ts's brandHueHex), so no per-call-site workaround is
// needed here.
export default function AppleIcon() {
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
          size="lg"
          tone="brand"
        />
      </div>
    ),
    { ...size },
  );
}
