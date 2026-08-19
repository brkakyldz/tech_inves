/**
 * The verifier warning banner (ADR 0010 §6, plan Faz 5.3).
 *
 * ## What this component is
 *
 * ADR 0010 §6 decided that a blocked draft is rendered to its reader rather
 * than withheld, and that ADR's Consequences section names the consequence:
 * "the banner is the only thing standing between a blocked draft and being
 * read as a finished one, which makes it a correctness-relevant UI element,
 * not decoration." Everything below follows from taking that literally.
 *
 * ## Non-suppressibility, and how it is enforced rather than requested
 *
 * There is no dismiss button, no collapse, no `localStorage`, no query
 * parameter and no prop that hides this. That is easy to write once and easy
 * to erode later, so the property is defended in three ways that do not rely
 * on anyone reading this comment:
 *
 * 1. **It is a server component.** No `"use client"` directive, so it ships
 *    no JavaScript to the browser at all. A dismiss button is not a small
 *    edit here — it requires converting the file to a client component
 *    first, which is a visible, reviewable change of kind.
 * 2. **The decision layer takes no suppression input.** `buildBannerModel`
 *    (`lib/verifier/banner.ts`) is a pure function of the report's own
 *    verdict, violations and partiality. It has no parameter that could
 *    carry "the user hid this", so there is nowhere for such a parameter to
 *    be threaded through.
 * 3. **A test asserts it.** `front-end/tests/verifier-banner.test.mjs` fails
 *    if this file gains `"use client"`, a `<button>`, an `onClick`, a
 *    `hidden`/`aria-hidden` attribute, a `display:none`, a `<details>`
 *    element, or any reference to `localStorage`/`sessionStorage`/`cookie`.
 *    Someone adding a dismiss control has to delete an assertion that says,
 *    in words, why it exists. That is the actual guard; this comment is only
 *    the explanation.
 *
 * ## Accessibility
 *
 * - **Announced.** The container carries `role="alert"` (implicitly
 *   `aria-live="assertive"`) for the three severe levels and `role="status"`
 *   (`aria-live="polite"`) for the advisory one, so a screen reader treats a
 *   blocked report differently from a flagged one. It is a landmark-bearing
 *   region with an accessible name via `aria-labelledby`, not a styled
 *   `<div>`.
 * - **Survives CSS being disabled.** The markup is ordinary semantic HTML in
 *   reading order — a heading, a paragraph, a heading, a list — placed first
 *   in the document flow. With every stylesheet removed it still renders
 *   above the report, in order, fully legible. Nothing here is positioned,
 *   floated, or pulled into place by CSS.
 * - **Colour is never the only carrier.** The severity is stated in words in
 *   the heading itself ("Blocked", "Known gaps", "Unverified", "Flagged"),
 *   the glyph beside it is decorative and `aria-hidden` so it adds nothing a
 *   listener has to decode, and each violation is prefixed with its category
 *   spelled out in text. Rendered in greyscale,
 *   or by a screen reader, or in a terminal browser, the severity is still
 *   fully conveyed.
 */

import * as React from "react";

import { buildBannerModel, type BannerLevel } from "@/lib/verifier/banner";
import type { VerifierViolation } from "@/lib/data/types";

/**
 * Per-level presentation. `word` is the part that carries severity without
 * colour and is the only thing a screen reader or an unstyled render needs;
 * `glyph` is purely decorative reinforcement and is therefore marked
 * `aria-hidden` at every use site rather than being given a label of its own.
 */
const LEVEL_PRESENTATION: Record<
  BannerLevel,
  {
    word: string;
    glyph: string;
    role: "alert" | "status";
    container: string;
    chip: string;
  }
> = {
  critical: {
    word: "Blocked",
    glyph: "⛔",
    role: "alert",
    container:
      "border-2 border-destructive bg-destructive/10 text-foreground",
    chip: "bg-destructive text-white",
  },
  serious: {
    word: "Known gaps",
    glyph: "⚠",
    role: "alert",
    container: "border-2 border-amber-600 bg-amber-500/10 text-foreground",
    chip: "bg-amber-600 text-white",
  },
  unknown: {
    word: "Unverified",
    glyph: "?",
    role: "alert",
    container: "border-2 border-foreground/60 bg-muted text-foreground",
    chip: "bg-foreground text-background",
  },
  advisory: {
    word: "Flagged",
    glyph: "!",
    role: "status",
    container: "border border-border bg-muted text-foreground",
    chip: "bg-foreground/80 text-background",
  },
};

export interface VerifierBannerProps {
  verdict: string | null | undefined;
  violations: VerifierViolation[] | null | undefined;
  isPartial: boolean;
}

/**
 * Note the prop list: three facts about the report, and nothing else. There
 * is deliberately no `dismissible`, no `variant`, no `compact` and no
 * `className` — a caller cannot ask for a quieter banner, because being
 * quiet on a blocked report is the failure this element exists to prevent.
 */
export function VerifierBanner({
  verdict,
  violations,
  isPartial,
}: VerifierBannerProps) {
  const model = buildBannerModel({ verdict, violations, isPartial });
  if (model === null) return null;

  const presentation = LEVEL_PRESENTATION[model.level];
  const headingId = "verifier-banner-heading";
  const itemsHeadingId = "verifier-banner-items-heading";

  return (
    <section
      role={presentation.role}
      aria-labelledby={headingId}
      data-verifier-level={model.level}
      className={`mb-8 rounded-md p-4 sm:p-5 ${presentation.container}`}
    >
      <h2
        id={headingId}
        className="font-serif text-lg font-semibold tracking-tight text-balance sm:text-xl"
      >
        <span
          className={`mr-2 inline-block rounded px-2 py-0.5 align-middle text-xs font-bold tracking-wide uppercase ${presentation.chip}`}
        >
          <span aria-hidden="true">{presentation.glyph} </span>
          {presentation.word}
        </span>
        {/*
          A literal space, not the chip's `mr-2`: with stylesheets disabled
          the margin is gone and "Known gapsThis report is published..." is
          what the reader gets. The gap has to be text.
        */}
        {" "}
        {model.title}
      </h2>

      <p className="mt-2 text-sm leading-relaxed text-pretty">
        {model.explanation}
      </p>

      <h3 id={itemsHeadingId} className="mt-4 text-sm font-semibold">
        {model.itemsHeading}
      </h3>

      {model.items.length > 0 && (
        <ul aria-labelledby={itemsHeadingId} className="mt-2 space-y-1.5">
          {model.items.map((item, index) => (
            <li
              // Violations are not individually identified by the pipeline
              // and the same message can legitimately repeat for different
              // sections, so the index is the only stable key available.
              // The list is never reordered client-side (this component
              // ships no JavaScript), so an index key is safe here.
              key={`${item.label}-${item.section ?? ""}-${index}`}
              className="text-sm leading-relaxed"
            >
              <strong className="font-semibold">{item.label}</strong>
              {item.section ? <span> ({item.section})</span> : null}
              <span>: </span>
              <span>{item.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The severity chip on its own, for list contexts (the report archive, the
 * landing page's report preview) where the full banner would not fit.
 *
 * This is a *pointer* to the banner, never a replacement for it: it carries
 * the same severity word, and it is only ever rendered next to a link to the
 * report, where the real banner is unavoidable. It is not suppressible for
 * the same reasons the banner is not, and it is derived from the same
 * `buildBannerModel` call, so the two can never disagree about whether a
 * report is sound.
 *
 * Its accessible name spells the severity out in full, because "Blocked"
 * next to a headline is unambiguous to a sighted reader scanning the list
 * but not to someone hearing the page linearly.
 */
export function VerifierStatusChip({
  verdict,
  violations,
  isPartial,
}: VerifierBannerProps) {
  const model = buildBannerModel({ verdict, violations, isPartial });
  if (model === null) return null;
  const presentation = LEVEL_PRESENTATION[model.level];
  return (
    <span
      data-verifier-level={model.level}
      className={`inline-block rounded px-2 py-0.5 text-xs font-bold tracking-wide uppercase ${presentation.chip}`}
    >
      <span aria-hidden="true">{presentation.glyph} </span>
      <span className="sr-only">Verifier status: </span>
      {presentation.word}
    </span>
  );
}

export default VerifierBanner;
