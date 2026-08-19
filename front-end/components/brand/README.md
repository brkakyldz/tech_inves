# components/brand

Procedurally generated identity assets. Everything here is a Server
Component (or a pure function with no React/DOM at all) — none of this ships
generation logic to the client.

## `lib/sigil.ts` (in `front-end/lib/`, not this folder)

Pure, dependency-free parameter derivation. `hashTicker(ticker)` is a
5-line FNV-1a 32-bit hash; `sigilParams(ticker, score, subscores)` turns
that hash plus the live score data into a `SigilParams` object: hue and
base rotation are stable per ticker (from the hash), ring fill-arc and
spoke lengths are continuous functions of the score/subscores. All emitted
numbers are rounded to 3 decimals so it is byte-identical across renders —
verify with the same input twice if you touch this file.

`null` subscores/score are a distinct "absent" state, not a 0 — see the
`absent`/`ringAbsent` flags. Do not treat them as zero in a renderer.

## `ScoreSigil.tsx`

The mark: a ring (composite score → filled arc) plus one spoke per
category subscore, evenly spaced by angle. `size="sm"` (~32px, card
ornament) and `size="lg"` (~200px, hero/OG) are genuinely different
drawings — `sm` drops the baseline ring and center dot and thickens every
stroke so it stays a legible silhouette instead of turning to mud, it does
not just get scaled down via CSS.

```tsx
<ScoreSigil ticker="AAPL" score={84.2} subscores={[90, 70, null, 60]} size="lg" />
```

Decorative by default (`aria-hidden`). Pass `title` when the sigil is the
only content conveying information (e.g. a standalone hero) — that renders
an SVG `<title>` and sets `role="img"` instead.

### Color / `tone`

**The sigil never encodes score quality through hue — the `lib/scoreColor.ts`
ramp owns that channel exclusively.** `tone` is `"neutral" | "brand"`,
default `"neutral"`:

- **`tone="neutral"` (default).** Every stroke — baseline track, absent
  markers, ring fill-arc, spokes — draws in `currentColor`. No hue at all.
  This is the variant used on score cards and anywhere else a score-color
  badge sits nearby: a hue-colored sigil next to a "Strong · 82" badge would
  read as a second, possibly-contradicting judgment about the same number
  (this happened for real — see the follow-up section below). The existing
  opacity hierarchy (baseline faint, absent markers reduced, fill/spokes
  full) still carries the drawing without color.
- **`tone="brand"`.** An opt-in, constrained hue band — `250deg`–`310deg`
  (blue → violet), fixed at `L=0.62 C=0.11` — rather than the full 360°
  wheel. The ticker hash still picks a stable point inside that band (via
  `brandHue()` in `lib/sigil.ts`), so per-company variation survives, but
  the band excludes every hue the score ramp uses for quality
  (green/amber/red-orange), so a brand-toned sigil can never be misread as
  "good" or "bad". Use this only where identity matters and no score color
  competes: the wordmark, OG images, a company-detail hero.

Both band endpoints are WCAG 1.4.11-verified ≥3:1 against `#ffffff`
(`H=250deg` → 3.624:1, `H=310deg` → 3.815:1, computed from the WCAG
relative-luminance formula — see the follow-up section below for the
derivation). Deliberately does not read `--chart-*` tokens.

**`tone="brand"` is emitted as hex, not `oklch()`.** `ScoreSigil` renders in
two environments: the browser DOM (the live site) and Satori, the renderer
behind `next/og`'s `ImageResponse` (OG images, `icon.tsx`, `apple-icon.tsx`).
Satori silently drops `oklch()` color strings — no error, the stroke just
renders as nothing — confirmed with an isolated repro during the Phase 6
share-layer work (see
`reports/agents/2026-08-17_fe-phase6-share-layer.md`). So `hueColor()`'s
math still happens in OKLCH (`brandHue()` in `lib/sigil.ts` picks the hue,
fixed at `L=0.62 C=0.11`), but the last step converts to sRGB hex via
`oklchToHex()`/`brandHueHex()` in `lib/sigil.ts` before it ever reaches a
`style` prop. **Do not "modernize" this back to a raw `oklch()` string** —
it will look identical in a local browser check and then silently blank out
the ring fill-arc and every spoke in every generated OG image, exactly the
regression this fix closed. If you touch the L/C/hue band, re-derive the hex
via `oklchToHex`, don't hand-round a new value.

The ticker hash always drives base rotation (in both tones) — that's what
keeps per-company recognizability once hue is constrained or removed.

## `Wordmark.tsx`

The "TechInves" lockup — sigil + wordform, for the header and standalone
brand placements. Uses a fixed signature input (`BRAND_TICKER =
"TECHINVES"`, constant score/subscores), not a real company's data — the
brand mark is not any one company's card. `size="sm" | "md" | "lg"`. Renders
its `ScoreSigil` with `tone="brand"` — the wordmark is identity, not a score
readout, so the constrained hue band is appropriate here even though score
cards stay `tone="neutral"`. The wordform uses `font-serif` (Fraunces once
wired up) — never hardcode a family here.

## `CohortGlyph.tsx`

Three hand-drawn 24×24 glyphs, one per cohort, styled to match
lucide-react exactly (`stroke-width="2"`, round caps/joins, `fill="none"`,
`stroke="currentColor"`, ~1px canvas padding, 2px corner radius on shapes
≥8px) so they sit next to real lucide icons without looking foreign:

- `A` — Yazılım & İnternet: a browser window with a code `<>` bracket pair.
- `B` — Donanım, Yarı İletken & Uzay: an IC/chip package with pins.
- `C` — IT Hizmetleri & Altyapı: a two-unit server rack with an uplink dot.

```tsx
<CohortGlyph cohort="A" size={20} />
```

## `grain.ts`

A ~200×200 `feTurbulence` grain tile, baked once and exported as a data
URI — not a live SVG `<filter>` applied to DOM content (that path
recomputes every frame and is the expensive one; baking sidesteps it).
Exports `GRAIN_DATA_URI` (raw string) and `GRAIN_BACKGROUND_STYLE`
(ready-to-spread `CSSProperties`, `background-repeat: repeat`,
`background-size` pinned to the tile). Do not stretch the tile to fill a
surface — tile it.

```tsx
<div style={GRAIN_BACKGROUND_STYLE} className="absolute inset-0" />
```

## Follow-up (2026-08-17): colour semantics defect fixed

Original implementation (`7c77bbb`) hashed the ticker to a hue anywhere on
the full 360° wheel and applied it unconditionally to the ring arc and
spokes. Rendering real data at `/sigil-preview` showed the defect directly:
AAPL (composite 82, strong) rendered **red**; INTC (composite 34, weak)
*also* rendered red; MSFT rendered green; IBM landed on a low-contrast olive
(~90–110°, which also fails WCAG 1.4.11 at the original `L=0.62 C=0.11`).
This re-created, in the sigil, exactly the kind of parallel/contradicting
color system that Phase 2 was fixing in `lib/scoreColor.ts` — hue on the
sigil carried no relationship to the score-quality hue the ramp in
`lib/scoreColor.ts` was establishing next to it.

**Fix.** `ScoreSigil` gained a `tone` prop (`"neutral" | "brand"`, default
`"neutral"`) — see the "Color / `tone`" section above. `Wordmark.tsx` now
passes `tone="brand"` explicitly; every other current call site (score
cards) is unchanged and therefore defaults to `"neutral"`, i.e. `currentColor`
only. `lib/sigil.ts` gained `BRAND_HUE_START_DEG = 250`, `BRAND_HUE_SPAN_DEG
= 60`, and a pure `brandHue(hue)` mapper that folds the existing full-wheel,
hash-derived `hue` into that band — determinism and per-ticker variation are
preserved, only the reachable hue range shrank. Base rotation (also
hash-derived) is untouched in both tones.

**Contrast verification** (WCAG relative-luminance formula, computed with a
manual OKLCH → linear-sRGB conversion, not estimated — script:
`contrast.mjs`, band fixed at `L=0.62 C=0.11`, the same L/C the original
implementation used):

| H (deg) | sRGB (linear→gamma) | Contrast vs `#ffffff` | 3:1 (WCAG 1.4.11 non-text) |
|---|---|---|---|
| 250 (band start) | `#4f8ac6` | 3.624:1 | PASS |
| 260 | `#5e86c8` | 3.662:1 | PASS |
| 270 | `#6c82c9` | 3.699:1 | PASS |
| 280 | `#797ec7` | 3.734:1 | PASS |
| 290 | `#857ac4` | 3.765:1 | PASS |
| 300 | `#9076be` | 3.792:1 | PASS |
| 310 (band end) | `#9a73b8` | 3.815:1 | PASS |

Both endpoints and everything between clear 3:1 at the existing `L=0.62
C=0.11` — no lightness reduction was needed once the band excluded the
90–110° olive region. If the band or the fixed L/C in `ScoreSigil.tsx`'s
`hueColor()` ever change, re-run this check; do not assume a shifted band
still clears 3:1.

**The rule going forward:** the sigil never encodes score quality through
hue, in either tone. The score ramp (`lib/scoreColor.ts`) is the only place
hue means "good" or "bad" in this codebase.
