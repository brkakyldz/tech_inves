// Server Component. The "TechInves" lockup: sigil + wordform, for the site
// header and standalone brand placements. Composed from ScoreSigil (itself a
// Server Component) so no client JS is involved.
import { ScoreSigil, type SigilSize } from "./ScoreSigil";

export type WordmarkSize = "sm" | "md" | "lg";

interface WordmarkProps {
  size?: WordmarkSize;
  className?: string;
}

// The brand mark is not any one company's card -- it needs its own fixed,
// deterministic "signature" input rather than real score data (using a real
// ticker would misleadingly imply the wordmark IS that company's score).
// These constants are the site's own signature and never change.
const BRAND_TICKER = "TECHINVES";
const BRAND_SCORE = 72;
const BRAND_SUBSCORES = [68, 80, 55, 74];

interface SizeConfig {
  sigilSize: SigilSize;
  sigilClass: string;
  textClass: string;
  gapClass: string;
}

const SIZE_CONFIG: Record<WordmarkSize, SizeConfig> = {
  sm: { sigilSize: "sm", sigilClass: "h-4 w-4", textClass: "text-sm", gapClass: "gap-1.5" },
  md: { sigilSize: "sm", sigilClass: "h-6 w-6", textClass: "text-xl", gapClass: "gap-2" },
  lg: { sigilSize: "lg", sigilClass: "h-10 w-10", textClass: "text-3xl", gapClass: "gap-3" },
};

export function Wordmark({ size = "md", className }: WordmarkProps) {
  const cfg = SIZE_CONFIG[size];
  return (
    <span
      className={`inline-flex items-center ${cfg.gapClass} ${className ?? ""}`}
    >
      <ScoreSigil
        ticker={BRAND_TICKER}
        score={BRAND_SCORE}
        subscores={BRAND_SUBSCORES}
        size={cfg.sigilSize}
        tone="brand"
        className={cfg.sigilClass}
      />
      {/* font-serif resolves to Fraunces once the typography phase wires it
          up -- never hardcode a family here. */}
      <span className={`font-serif leading-none tracking-tight ${cfg.textClass}`}>
        TechInves
      </span>
    </span>
  );
}
