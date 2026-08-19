/**
 * Tests for the verifier warning banner (ADR 0010 §6, plan Faz 5.3).
 *
 * ## Why these exist at all
 *
 * ADR 0010 §6 relaxed a safety property — a blocked draft is rendered to the
 * reader instead of being withheld — and its Consequences section says what
 * that costs: "the banner is the only thing standing between a blocked draft
 * and being read as a finished one, which makes it a correctness-relevant UI
 * element, not decoration." Correctness-relevant elements get tests.
 *
 * ## Why `node --test` and no test framework
 *
 * The front-end had no test runner and adding one (vitest/jest + a DOM
 * implementation + a transform pipeline) would have meant several new
 * dependencies to install and maintain for the sake of a handful of
 * assertions. Node's built-in runner plus two packages already present in
 * `node_modules` — `jiti` (ships with Next, transpiles TS/TSX at require
 * time) and `react-dom/server` — covers it with no new dependency at all.
 * The same choice `.claude/hooks/tests/reports-hooks.test.mjs` already makes
 * elsewhere in this repository.
 *
 * Run with `npm test` from `front-end/`.
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

const { buildBannerModel } = await jiti.import("../lib/verifier/banner.ts");
const { VerifierBanner, VerifierStatusChip } = await jiti.import(
  "../components/reports/VerifierBanner.tsx",
);

const BANNER_SOURCE_PATH = path.join(
  ROOT,
  "components",
  "reports",
  "VerifierBanner.tsx",
);
const BANNER_SOURCE = readFileSync(BANNER_SOURCE_PATH, "utf8");
/**
 * The same file with every comment removed. The suppression scan below runs
 * against this, not the raw text: the component's own doc comment explains
 * what it must never contain, and naming a forbidden token in prose is not
 * the same as using it.
 */
const BANNER_CODE = BANNER_SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(
  /^\s*\/\/.*$/gm,
  "",
);
const DETAIL_PAGE_SOURCE = readFileSync(
  path.join(ROOT, "app", "reports", "[slug]", "page.tsx"),
  "utf8",
);

const render = (props) => renderToStaticMarkup(VerifierBanner(props));

/** Two compliance-hard violations of different categories, plus a soft one. */
const BLOCK_VIOLATIONS = [
  {
    severity: "soft",
    category: "low_reliability_label",
    message: "missing 'low reliability' label for SNOW",
    section: "SNOW",
  },
  {
    severity: "compliance_hard",
    category: "citation",
    message:
      "fabricated citation (URL never retrieved): https://example.com/invented-article",
    section: "NVDA",
  },
  {
    severity: "compliance_hard",
    category: "number_leak",
    message: "number not found in scores/financials: '41.7%'",
    section: "MSFT",
  },
];

const DEGRADED_VIOLATIONS = [
  {
    severity: "structural_hard",
    category: "completeness",
    message: "watchlist ticker never mentioned: WDAY",
    section: null,
  },
];

// ---------------------------------------------------------------------------
// The two banner tests the phase gate names.
// ---------------------------------------------------------------------------

test("a block report renders every violation it was blocked for", () => {
  const html = render({
    verdict: "block",
    violations: BLOCK_VIOLATIONS,
    isPartial: false,
  });

  // The verdict is stated, in words, not only in colour.
  assert.match(html, /Blocked/);
  assert.match(html, /blocked by the verifier/i);
  assert.match(html, /data-verifier-level="critical"/);

  // ADR 0010 §6's actual requirement: the violations are *named*. A reader
  // must learn what is wrong without opening the database.
  for (const violation of BLOCK_VIOLATIONS) {
    assert.ok(
      html.includes(escapeHtml(violation.message)),
      `banner did not name the violation: ${violation.message}`,
    );
  }
  // ...including which section each was attributed to.
  assert.match(html, /\(NVDA\)/);
  assert.match(html, /\(MSFT\)/);
  // ...and each carries a human-readable category label.
  assert.match(html, /Fabricated citation/);
  assert.match(html, /Unverifiable number/);

  assert.match(html, /3 issues recorded:/);

  // Compliance-hard violations sort above the soft one: the reason the
  // report is unpublishable should not be below a formatting nit.
  const firstHard = html.indexOf("Fabricated citation");
  const soft = html.indexOf("Missing reliability label");
  assert.ok(firstHard > -1 && soft > -1 && firstHard < soft);
});

test("a degraded_publish report also carries a banner, distinguishable from block", () => {
  const html = render({
    verdict: "degraded_publish",
    violations: DEGRADED_VIOLATIONS,
    isPartial: false,
  });

  // It exists at all. Without this, structural-hard violations ship silently
  // — the report renders looking finished while missing coverage it promised.
  assert.match(html, /data-verifier-level="serious"/);
  assert.match(html, /published with known gaps/i);
  assert.ok(html.includes(escapeHtml(DEGRADED_VIOLATIONS[0].message)));

  // It is not the block treatment. A reader who cannot tell the two apart
  // learns to discount both, which costs the block banner its meaning.
  assert.doesNotMatch(html, /data-verifier-level="critical"/);
  assert.doesNotMatch(html, /blocked by the verifier/i);
  assert.match(html, /Known gaps/);

  // Still assertive: this is a defect in the document, not a footnote.
  assert.match(html, /role="alert"/);
});

// ---------------------------------------------------------------------------
// The rest of the four-valued vocabulary, and the fifth state.
// ---------------------------------------------------------------------------

test("pass_with_flags is visible but calm", () => {
  const html = render({
    verdict: "pass_with_flags",
    violations: [
      {
        severity: "soft",
        category: "completeness",
        message: "only 3 deep-dive sections",
        section: null,
      },
    ],
    isPartial: false,
  });
  assert.match(html, /data-verifier-level="advisory"/);
  assert.match(html, /passed with flags/i);
  assert.ok(html.includes("only 3 deep-dive sections"));
  // Polite, not assertive — soft issues are information, not alarm.
  assert.match(html, /role="status"/);
  assert.doesNotMatch(html, /role="alert"/);
});

test("a clean pass on a full watchlist run renders nothing", () => {
  assert.equal(render({ verdict: "pass", violations: [], isPartial: false }), "");
  assert.equal(
    buildBannerModel({ verdict: "pass", violations: [], isPartial: false }),
    null,
  );
});

test("a null verdict is treated as unverified, never as a pass", () => {
  const html = render({ verdict: null, violations: null, isPartial: false });

  assert.notEqual(html, "", "a report with no verdict rendered no banner");
  assert.match(html, /data-verifier-level="unknown"/);
  assert.match(html, /Unverified/);
  assert.match(html, /no verifier verdict/i);
  // It must not borrow any of the reassuring language of a pass.
  assert.doesNotMatch(html, /passed/i);
  // And it must say why there is no violation list, rather than implying
  // the list is empty because nothing was found.
  assert.match(html, /No violation detail was stored/i);
});

test("null violations and an empty violation list are different states", () => {
  const unknown = buildBannerModel({
    verdict: "block",
    violations: null,
    isPartial: false,
  });
  const empty = buildBannerModel({
    verdict: "block",
    violations: [],
    isPartial: false,
  });
  assert.notEqual(unknown.itemsHeading, empty.itemsHeading);
  assert.match(unknown.itemsHeading, /No violation detail was stored/i);
  assert.match(empty.itemsHeading, /recorded no specific violations/i);
});

test("an unrecognised verdict fails loud rather than rendering clean", () => {
  const html = render({
    verdict: "probably_fine",
    violations: [],
    isPartial: false,
  });
  assert.match(html, /data-verifier-level="critical"/);
  assert.match(html, /unrecognised verifier verdict/i);
  assert.match(html, /probably_fine/);
});

test("a partial run says so, even when the verdict is clean", () => {
  const html = render({ verdict: "pass", violations: [], isPartial: true });
  assert.notEqual(html, "");
  assert.match(html, /part of the watchlist/i);
  assert.match(html, /fewer tickers than the full watchlist/i);
});

test("partiality is listed alongside the violations, not instead of them", () => {
  const model = buildBannerModel({
    verdict: "block",
    violations: DEGRADED_VIOLATIONS,
    isPartial: true,
  });
  assert.equal(model.level, "critical");
  assert.equal(model.items.length, 2);
  assert.ok(model.items.some((i) => i.label === "Partial run"));
});

// ---------------------------------------------------------------------------
// Non-suppressibility. These are the assertions that stop a future
// contributor from adding a dismiss control — deleting one is a deliberate,
// visible act, which a comment asking nicely is not.
// ---------------------------------------------------------------------------

test("the banner ships no way to hide itself", () => {
  // Rendered output, across every level the banner can take.
  const cases = [
    { verdict: "block", violations: BLOCK_VIOLATIONS, isPartial: false },
    { verdict: "degraded_publish", violations: DEGRADED_VIOLATIONS, isPartial: false },
    { verdict: "pass_with_flags", violations: [], isPartial: false },
    { verdict: null, violations: null, isPartial: false },
  ];
  for (const props of cases) {
    const html = render(props);
    assert.doesNotMatch(html, /<button/i, `${props.verdict}: rendered a button`);
    assert.doesNotMatch(html, /<details/i, `${props.verdict}: rendered a <details> (collapsible)`);
    assert.doesNotMatch(html, /\shidden(=|\s|>)/i, `${props.verdict}: rendered a hidden attribute`);
    assert.doesNotMatch(html, /display:\s*none/i, `${props.verdict}: rendered display:none`);
    // aria-hidden appears on the decorative glyph and nowhere else; it must
    // never be on an element carrying text.
    assert.doesNotMatch(
      html,
      /aria-hidden="true"[^>]*>[^<]*[A-Za-z]{3}/,
      `${props.verdict}: aria-hidden on an element carrying words`,
    );
  }
});

test("the banner source contains no suppression machinery", () => {
  // If you are here because this test failed: it did its job. This banner is
  // the only thing standing between a blocked draft and being read as a
  // finished report (ADR 0010 §6, Consequences). A dismiss button, a
  // collapse, a "don't show again" flag or a query parameter that hides it
  // all turn a correctness-relevant element into decoration. Changing that
  // is an ADR-level decision, not a UI tweak — amend ADR 0010 §6 first, and
  // delete this test in the same commit that does.
  const forbidden = [
    ["use client", /["']use client["']/],
    ["onClick handler", /onClick/],
    ["useState", /useState/],
    ["localStorage", /localStorage/],
    ["sessionStorage", /sessionStorage/],
    ["document.cookie", /document\.cookie/],
    ["searchParams", /searchParams/],
    ["a dismiss/hide/collapse prop", /\b(dismiss|dismissible|collapsed|collapsible|suppress|hideBanner)\b/i],
  ];
  for (const [name, pattern] of forbidden) {
    assert.doesNotMatch(
      BANNER_CODE,
      pattern,
      `VerifierBanner.tsx must not contain ${name} — see the note in this test`,
    );
  }
});

test("buildBannerModel takes no input that could suppress it", () => {
  // The decision layer is a pure function of the report's own facts. If a
  // caller could pass anything else, "hide it for this user" becomes a
  // one-line change; as it stands there is nowhere to put it.
  assert.equal(buildBannerModel.length, 1);
  const model = buildBannerModel({
    verdict: "block",
    violations: BLOCK_VIOLATIONS,
    isPartial: false,
    // Extra keys are ignored, not honoured.
    dismissed: true,
    hidden: true,
    suppress: true,
  });
  assert.notEqual(model, null);
  assert.equal(model.level, "critical");
});

test("the banner is the first element of the report page, above the content", () => {
  const bannerAt = DETAIL_PAGE_SOURCE.indexOf("<VerifierBanner");
  assert.ok(bannerAt > -1, "the report detail page does not render the banner");
  // Above the back-link, the title, the excerpt and the sections — position
  // in source order, so it survives with CSS disabled.
  const markers = [
    'href="/reports"', // the back-link
    "text-4xl font-semibold", // the report's own <h1>
    "{stripMarkdown(report.excerpt)}", // the lead paragraph
    "sections.map(", // every report section
  ];
  for (const marker of markers) {
    const at = DETAIL_PAGE_SOURCE.indexOf(marker);
    assert.ok(at > -1, `marker not found in the report page: ${marker}`);
    assert.ok(bannerAt < at, `the banner is rendered after ${marker}`);
  }
});

// ---------------------------------------------------------------------------
// Accessibility.
// ---------------------------------------------------------------------------

test("the banner is announced, named, and legible without CSS or colour", () => {
  const html = render({
    verdict: "block",
    violations: BLOCK_VIOLATIONS,
    isPartial: false,
  });

  // A region with a role, not a styled div, and with an accessible name
  // pointing at its own heading.
  assert.match(html, /<section[^>]+role="alert"/);
  assert.match(html, /aria-labelledby="verifier-banner-heading"/);
  assert.match(html, /id="verifier-banner-heading"/);

  // Semantic, in reading order: heading, explanation, list heading, list.
  const order = ["<h2", "<p", "<h3", "<ul", "<li"];
  let cursor = -1;
  for (const tag of order) {
    const at = html.indexOf(tag, cursor + 1);
    assert.ok(at > cursor, `expected ${tag} after position ${cursor}`);
    cursor = at;
  }

  // Severity is carried by words, so stripping every class attribute (the
  // CSS-disabled case) loses no information about how bad this is.
  const unstyled = html.replace(/\sclass="[^"]*"/g, "");
  assert.match(unstyled, /Blocked/);
  assert.match(unstyled, /blocked by the verifier/i);
  // The severity chip and the heading are separated by real whitespace, not
  // by a margin utility that vanishes with the stylesheet.
  assert.match(
    unstyled.replace(/<[^>]+>/g, ""),
    /Blocked\s+This report was blocked/,
  );
  assert.ok(unstyled.includes(escapeHtml(BLOCK_VIOLATIONS[1].message)));

  // No positioning that would have to be undone for the banner to appear
  // above the content — it is above the content because it is first.
  assert.doesNotMatch(html, /position:\s*(absolute|fixed)/i);
});

test("the archive chip states the severity in text for screen readers", () => {
  const html = renderToStaticMarkup(
    VerifierStatusChip({ verdict: "block", violations: null, isPartial: false }),
  );
  assert.match(html, /Verifier status: /);
  assert.match(html, /Blocked/);
  assert.match(html, /data-verifier-level="critical"/);

  assert.equal(
    renderToStaticMarkup(
      VerifierStatusChip({ verdict: "pass", violations: [], isPartial: false }),
    ),
    "",
  );
});

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#x27;");
}
