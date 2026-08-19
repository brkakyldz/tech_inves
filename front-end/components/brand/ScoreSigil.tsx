// Server Component -- no "use client". Sigil generation is pure geometry
// derived from lib/sigil.ts and must never ship to the client: shipping it
// would mean shipping the hash/parameter-derivation logic (and re-running it
// 40x per client render) for zero benefit, since the output is static markup.
import { brandHueHex, sigilParams } from "@/lib/sigil";

export type SigilSize = "sm" | "lg";

/**
 * "neutral" (default): draws entirely in `currentColor` -- no hue at all.
 * This is the variant for anywhere a score color (the `lib/scoreColor.ts`
 * ramp) appears nearby, e.g. score cards -- a colored sigil next to a
 * colored score badge would read as a second, contradicting judgment about
 * the same number.
 *
 * "brand": an opt-in, constrained blue->violet hue band (see
 * `brandHue`/`BRAND_HUE_START_DEG` in lib/sigil.ts) for places where
 * identity matters and no score color competes -- the wordmark, OG images,
 * a company-detail hero. The band excludes every hue the score ramp uses,
 * so it can never be misread as a performance signal.
 *
 * The sigil never encodes score quality through hue in either tone; the
 * score ramp owns that channel exclusively.
 */
export type SigilTone = "neutral" | "brand";

interface ScoreSigilProps {
  ticker: string;
  score: number | null;
  subscores: (number | null)[];
  /** "sm" (~32px, card ornament) or "lg" (~200px+, hero/OG). Default "lg". */
  size?: SigilSize;
  /** "neutral" (default, currentColor) or "brand" (constrained hue band). See `SigilTone`. */
  tone?: SigilTone;
  /**
   * Accessible title. Provide this when the sigil stands alone as meaningful
   * content (e.g. a company detail hero). Omit for decorative instances --
   * those get aria-hidden instead.
   */
  title?: string;
  className?: string;
}

interface SizeConfig {
  px: number;
  ringStroke: number;
  ringAbsentStroke: number;
  spokeStroke: number;
  spokeInnerR: number;
  spokeOuterR: number;
  absentTickLen: number;
  showBaseline: boolean;
  showCenterDot: boolean;
}

// The two supported scales are genuinely different drawings, not one drawing
// scaled by CSS: "sm" drops the baseline ring and center dot and thickens
// every stroke so the mark stays a legible ring+spokes silhouette instead of
// mud at 32px. "lg" adds the fine baseline ring and center anchor that only
// read cleanly at hero/OG size.
const SIZE_CONFIG: Record<SigilSize, SizeConfig> = {
  sm: {
    px: 32,
    ringStroke: 7,
    ringAbsentStroke: 6,
    spokeStroke: 7,
    spokeInnerR: 20,
    spokeOuterR: 40,
    absentTickLen: 5,
    showBaseline: false,
    showCenterDot: false,
  },
  lg: {
    px: 200,
    ringStroke: 3,
    ringAbsentStroke: 2.5,
    spokeStroke: 2.5,
    spokeInnerR: 14,
    spokeOuterR: 41,
    absentTickLen: 4,
    showBaseline: true,
    showCenterDot: true,
  },
};

const CX = 50;
const CY = 50;
const RING_R = 46;

function polar(cx: number, cy: number, r: number, angleDeg: number): [number, number] {
  const rad = (angleDeg * Math.PI) / 180;
  // 0deg = up, clockwise -- matches lib/sigil.ts's angle convention.
  return [cx + r * Math.sin(rad), cy - r * Math.cos(rad)];
}

export function ScoreSigil({
  ticker,
  score,
  subscores,
  size = "lg",
  tone = "neutral",
  title,
  className,
}: ScoreSigilProps) {
  const params = sigilParams(ticker, score, subscores);
  const cfg = SIZE_CONFIG[size];
  const circumference = 2 * Math.PI * RING_R;
  const filledLen = params.ringFillFrac * circumference;
  // "neutral" draws in currentColor -- no hue, so it can never compete with
  // (or contradict) a nearby score-color badge. "brand" is the sole opt-in
  // path to color, and only within the constrained band -- never the full
  // wheel, never a hue the score ramp uses. Resolved to hex (not oklch())
  // because this component renders in both the DOM and Satori (next/og's
  // ImageResponse), and Satori silently drops oklch() strings -- see
  // components/brand/README.md.
  const accent = tone === "brand" ? brandHueHex(params.hue) : "currentColor";

  return (
    <svg
      viewBox="0 0 100 100"
      width={cfg.px}
      height={cfg.px}
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : "true"}
    >
      {title ? <title>{title}</title> : null}

      {/* Baseline ring track -- lg only; at sm it would just add noise. */}
      {cfg.showBaseline && !params.ringAbsent && (
        <circle
          cx={CX}
          cy={CY}
          r={RING_R}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.15}
          strokeWidth={cfg.ringStroke}
        />
      )}

      {params.ringAbsent ? (
        // Composite score itself missing: a dashed full track, never a
        // filled arc -- a filled arc at any length asserts "measured".
        <circle
          cx={CX}
          cy={CY}
          r={RING_R}
          fill="none"
          stroke="currentColor"
          strokeOpacity={0.35}
          strokeWidth={cfg.ringAbsentStroke}
          strokeDasharray="3 4"
        />
      ) : (
        <circle
          cx={CX}
          cy={CY}
          r={RING_R}
          fill="none"
          stroke={accent}
          strokeWidth={cfg.ringStroke}
          strokeLinecap="round"
          strokeDasharray={`${filledLen} ${circumference}`}
          transform={`rotate(-90 ${CX} ${CY})`}
        />
      )}

      {cfg.showCenterDot && (
        <circle cx={CX} cy={CY} r={2.5} fill="currentColor" fillOpacity={0.3} />
      )}

      {params.spokes.map((spoke, i) => {
        if (spoke.absent) {
          // Short, low-opacity stub anchored at the inner radius -- reads
          // as "no data" rather than "measured at zero" even at 32px, where
          // a dash pattern would just disappear.
          const [x1, y1] = polar(CX, CY, cfg.spokeInnerR, spoke.angleDeg);
          const [x2, y2] = polar(
            CX,
            CY,
            cfg.spokeInnerR + cfg.absentTickLen,
            spoke.angleDeg,
          );
          return (
            <line
              key={i}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke="currentColor"
              strokeOpacity={0.35}
              strokeWidth={cfg.spokeStroke}
              strokeLinecap="round"
            />
          );
        }

        const outerR =
          cfg.spokeInnerR + (cfg.spokeOuterR - cfg.spokeInnerR) * spoke.lengthFrac;
        const [x1, y1] = polar(CX, CY, cfg.spokeInnerR, spoke.angleDeg);
        const [x2, y2] = polar(CX, CY, outerR, spoke.angleDeg);
        return (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke={accent}
            strokeWidth={cfg.spokeStroke}
            strokeLinecap="round"
          />
        );
      })}
    </svg>
  );
}
