/**
 * Tests for the run-to-run delta display (plan §8 Faz 7a).
 *
 * ## Why these exist
 *
 * Two defects have already shipped out of `components/motion/**`: a
 * paint-order bug in the score counter, and a zero-magnitude delta that
 * asserted a direction the printed number did not support (see `git log`
 * and `reports/backlog/phase5-motion-live-verification-blocked.md`). This
 * phase adds a *third* state to the same component family -- "there is no
 * delta" -- and the whole point of the phase is that that state must never
 * collapse into either of the other two.
 *
 * So the assertions below are about the three states staying apart:
 *
 *   delta > 0 / < 0   a direction, with an arrow and a sign
 *   delta === 0       measured, unchanged, and explicitly *no* direction
 *   delta === null    not measured; a stated reason, and no number at all
 *
 * Runner rationale: see the header of `verifier-banner.test.mjs`.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";
import { renderToStaticMarkup } from "react-dom/server";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const jiti = createJiti(import.meta.url, { alias: { "@": ROOT }, jsx: true });

const { DeltaIndicator, DeltaUnavailable, ScoreDeltaDisplay } = await jiti.import(
  "../components/motion/DeltaIndicator.tsx",
);
const { deltaUnavailableLabel, DELTA_UNAVAILABLE_LABEL, DELTA_UNAVAILABLE_FALLBACK } =
  await jiti.import("../lib/labels.ts");

/** The rendered markup with every `class` attribute stripped. Direction and
 * availability have to survive without CSS -- they are carried in glyphs and
 * words, not in hue (WCAG 1.4.1). */
const withoutClasses = (html) => html.replace(/ class="[^"]*"/g, "");

const render = (element) => renderToStaticMarkup(element);

/** `renderToStaticMarkup` HTML-escapes the label text, so compare like for
 * like rather than against the raw string. */
const escaped = (text) =>
  text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");

// --------------------------------------------------------------------------
// The three states, kept apart
// --------------------------------------------------------------------------

test("a positive delta renders a direction, a sign and a magnitude", () => {
  const html = withoutClasses(render(DeltaIndicator({ delta: 4.25 })));
  assert.match(html, /▲/);
  assert.match(html, /\+/);
  assert.match(html, /4\.3/);
  assert.match(html, /increased/);
  assert.doesNotMatch(html, /decreased|no change/);
});

test("a negative delta renders the opposite direction", () => {
  const html = withoutClasses(render(DeltaIndicator({ delta: -4.25 })));
  assert.match(html, /▼/);
  assert.match(html, /decreased/);
  assert.doesNotMatch(html, /increased|no change/);
});

test("a zero delta is unchanged, and carries no direction", () => {
  const html = withoutClasses(render(DeltaIndicator({ delta: 0 })));
  assert.match(html, /no change/);
  assert.doesNotMatch(html, /▲|▼/, "zero has no direction to point in");
  assert.doesNotMatch(html, /increased|decreased/);
  // Still a measurement, so the number is shown.
  assert.match(html, /0\.0/);
});

test("a delta that rounds away to zero does not regain a direction", () => {
  // The regression this file's second paragraph names: 0.02 prints as "0.0",
  // and an arrow next to "0.0" asserts a movement the printed number does
  // not support.
  for (const delta of [0.02, -0.02, 0.049, -0.049]) {
    const html = withoutClasses(render(DeltaIndicator({ delta })));
    assert.doesNotMatch(html, /▲|▼/, `${delta} should print as unchanged`);
    assert.match(html, /no change/);
  }
});

test("an unavailable delta shows a reason and no number", () => {
  const html = withoutClasses(
    render(DeltaUnavailable({ reason: "cohort_changed" })),
  );
  assert.match(html, /Cohort changed between runs/);
  assert.doesNotMatch(html, /▲|▼/);
  assert.doesNotMatch(html, /\d/, "there is no number to show, so show none");
});

// --------------------------------------------------------------------------
// ScoreDeltaDisplay: the branch every call site delegates to
// --------------------------------------------------------------------------

const availableDelta = {
  delta: -2.5,
  previousComposite: 70,
  previousRunId: "run-a",
  currentRunId: "run-b",
  unavailableReason: null,
};

const firstRunDelta = {
  delta: null,
  previousComposite: null,
  previousRunId: null,
  currentRunId: "run-b",
  unavailableReason: "first_run",
};

test("ScoreDeltaDisplay renders the indicator when a delta exists", () => {
  const html = withoutClasses(render(ScoreDeltaDisplay({ delta: availableDelta })));
  assert.match(html, /▼/);
  assert.match(html, /2\.5/);
});

test("the first-ever run renders as a normal state, not an error or a zero", () => {
  const html = withoutClasses(render(ScoreDeltaDisplay({ delta: firstRunDelta })));
  assert.match(html, /No earlier run to compare against/);
  // The three things it must not be mistaken for.
  assert.doesNotMatch(html, /0/, "a first run is not a delta of zero");
  assert.doesNotMatch(html, /▲|▼/);
  assert.doesNotMatch(html, /no change/, "nothing was measured, so nothing is unchanged");
});

test("an incomparable pair renders as unavailable, never as a number", () => {
  for (const reason of Object.keys(DELTA_UNAVAILABLE_LABEL)) {
    const html = withoutClasses(
      render(
        ScoreDeltaDisplay({
          delta: {
            delta: null,
            previousComposite: null,
            previousRunId: "run-a",
            currentRunId: "run-b",
            unavailableReason: reason,
          },
        }),
      ),
    );
    assert.ok(
      html.includes(escaped(DELTA_UNAVAILABLE_LABEL[reason])),
      `${reason} should render its own label`,
    );
    assert.doesNotMatch(html, /▲|▼/, `${reason} must not render a direction`);
  }
});

test("an unrecognised reason still says unavailable rather than rendering blank", () => {
  assert.equal(deltaUnavailableLabel("something_new"), DELTA_UNAVAILABLE_FALLBACK);
  assert.equal(deltaUnavailableLabel(null), DELTA_UNAVAILABLE_FALLBACK);
  const html = withoutClasses(
    render(DeltaUnavailable({ reason: "something_the_ui_has_never_seen" })),
  );
  assert.ok(html.includes(escaped(DELTA_UNAVAILABLE_FALLBACK)));
});

// --------------------------------------------------------------------------
// Call sites: no local delta arithmetic survives
// --------------------------------------------------------------------------

test("no page computes its own delta out of the score history series", () => {
  // The company page used to subtract the last two history points, which
  // cannot tell a comparable pair from an incomparable one. That arithmetic
  // is the API's job now (ADR 0009's data-boundary caution) and must not
  // reappear here.
  const source = readFileSync(
    path.join(ROOT, "app", "companies", "[ticker]", "page.tsx"),
    "utf8",
  ).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(source, /compositeScore\s*-\s*history/);
  assert.doesNotMatch(source, /history\[[^\]]*\]\.compositeScore\s*-/);
  assert.match(source, /score\.delta/);
});

test("no call site substitutes a zero for a missing delta", () => {
  for (const file of [
    path.join(ROOT, "app", "companies", "[ticker]", "page.tsx"),
    path.join(ROOT, "components", "landing", "ScoreCard.tsx"),
  ]) {
    const source = readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(
      source,
      /delta[^\n]*\?\?\s*0/,
      `${path.basename(file)} must not default a missing delta to zero`,
    );
  }
});

test("a payload with no delta field at all degrades to unavailable, not to a crash", () => {
  // Reachable through `lib/api/client.ts`'s tag-based fetch cache: a
  // response cached before this field existed still has to render.
  for (const missing of [null, undefined, {}]) {
    const html = withoutClasses(render(ScoreDeltaDisplay({ delta: missing })));
    assert.ok(html.includes(escaped(DELTA_UNAVAILABLE_FALLBACK)));
    assert.doesNotMatch(html, /▲|▼/);
    assert.doesNotMatch(html, /\d/);
  }
});
