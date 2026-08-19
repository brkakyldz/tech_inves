// Server Component. Three hand-drawn glyphs for the three cohorts, drawn to
// match lucide-react's own icon-design-guide numbers exactly (fetched
// directly from lucide.dev/contribute/icon-design-guide) so they sit next to
// real lucide-react icons without looking foreign:
//   viewBox 0 0 24 24, ~1px padding (~22x22 live area), stroke-width 2,
//   round caps/joins, fill none, stroke currentColor, 2px corner radius on
//   shapes >=8px, >=2px spacing between distinct elements.
import type { JSX, SVGProps } from "react";
import type { Cohort } from "@/lib/data/types";

interface CohortGlyphProps extends SVGProps<SVGSVGElement> {
  cohort: Cohort;
  size?: number;
  /** Accessible title. Omit for decorative use (default: aria-hidden). */
  title?: string;
}

const SHARED_PROPS = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

// A -- Yazilim & Internet (software & internet): a browser window frame
// with a code angle-bracket pair inside. The window disambiguates this from
// a plain "code" glyph -- it's specifically the *web/software* cohort.
function GlyphA() {
  return (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <polyline points="9,12 7,14.5 9,17" />
      <polyline points="15,12 17,14.5 15,17" />
    </>
  );
}

// B -- Donanim, Yari Iletken & Uzay (hardware, semiconductor & space): a
// classic IC/chip package -- a square die with two pins per side. Reads as
// "silicon" distinctly from a generic square.
function GlyphB() {
  return (
    <>
      <rect x="8" y="8" width="8" height="8" rx="2" />
      <line x1="10" y1="8" x2="10" y2="5" />
      <line x1="14" y1="8" x2="14" y2="5" />
      <line x1="10" y1="16" x2="10" y2="19" />
      <line x1="14" y1="16" x2="14" y2="19" />
      <line x1="8" y1="10" x2="5" y2="10" />
      <line x1="8" y1="14" x2="5" y2="14" />
      <line x1="16" y1="10" x2="19" y2="10" />
      <line x1="16" y1="14" x2="19" y2="14" />
    </>
  );
}

// C -- IT Hizmetleri & Altyapi (IT services & infrastructure): a two-unit
// server rack, each unit with a status light, plus an uplink line to a
// signal dot above -- the uplink is what reads as "infrastructure/service"
// rather than just "a server".
function GlyphC() {
  return (
    <>
      <rect x="4" y="8" width="16" height="6" rx="2" />
      <rect x="4" y="16" width="16" height="6" rx="2" />
      <circle cx="7.5" cy="11" r="0.5" fill="currentColor" />
      <circle cx="7.5" cy="19" r="0.5" fill="currentColor" />
      <line x1="16" y1="8" x2="16" y2="5" />
      <circle cx="16" cy="4" r="1" />
    </>
  );
}

const GLYPHS: Record<Cohort, () => JSX.Element> = {
  A: GlyphA,
  B: GlyphB,
  C: GlyphC,
};

export function CohortGlyph({
  cohort,
  size = 24,
  title,
  ...svgProps
}: CohortGlyphProps) {
  const Glyph = GLYPHS[cohort];
  return (
    <svg
      {...SHARED_PROPS}
      width={size}
      height={size}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : "true"}
      {...svgProps}
    >
      {title ? <title>{title}</title> : null}
      <Glyph />
    </svg>
  );
}
