import { Fraunces, Source_Sans_3, JetBrains_Mono, Newsreader } from "next/font/google";

// latin-ext is mandatory for Turkish: ğ/Ğ/ş/Ş/İ live outside the base latin
// subset and silently fall back without it (verified in-browser, see
// reports/plans/2026-08-17_frontend-design-system-plan.md §2).

export const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin", "latin-ext"],
  axes: ["SOFT", "WONK", "opsz"],
});

export const sourceSans = Source_Sans_3({
  variable: "--font-source-sans",
  subsets: ["latin", "latin-ext"],
});

export const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin", "latin-ext"],
});

// Long-form report prose only (/reports/[slug]) -- route-scoped, not loaded
// in the root layout (plan §2/§6 Phase 4). Newsreader has tabular figures by
// default (verified this session, see the design-system plan §2), so
// numerals inside report prose are safe here without the Fraunces trap.
export const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin", "latin-ext"],
  axes: ["opsz"],
});
