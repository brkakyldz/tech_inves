/**
 * Faz 6 (`reports/plans/2026-08-18_on-demand-transformation.md` §7,
 * ADR 0010 §7): the keyless demo state must be unmistakably labelled in the
 * UI, or the seeded fixture reports' verifier verdicts
 * (`src/techinves/api/seed_mock.py::SEED_VERIFIER_VERDICTS`) must not be
 * written at all -- a visitor has to be able to tell at a glance that they
 * are looking at seeded sample data, not the output of a real run.
 *
 * This is a server component (`app/layout.tsx`, above `SiteHeader`, so it is
 * the first thing on every page) reading `GET /v1/meta`'s `mode` field once
 * per request. It renders nothing in live mode and nothing if the API is
 * unreachable (`getAppMeta` never throws) -- an unreachable API is a
 * different problem this banner does not speak to.
 *
 * Deliberately separate from `VerifierBanner`
 * (`front-end/components/reports/VerifierBanner.tsx`, out of scope for this
 * phase): that banner is per-report and states a report's own verifier
 * verdict. This one is site-wide and states a fact about the deployment --
 * "you are looking at a self-hosted demo with no API keys configured" --
 * which is true of every page, not only report pages, and must not be
 * confusable with a verdict a visitor could dismiss as "just this report".
 */

import { getAppMeta } from "@/lib/data/meta";

export async function DemoModeBanner() {
  const meta = await getAppMeta();
  if (meta === null || meta.mode !== "demo") return null;

  return (
    <div
      role="status"
      data-demo-mode="true"
      className="border-b-2 border-foreground bg-foreground text-background"
    >
      <p className="content-wrap py-2 text-center text-xs font-medium">
        <span className="mr-1.5 uppercase tracking-wide">Demo mode</span>
        You are viewing seeded sample data — scores, financials and reports
        are fictional fixtures, not the output of a real run. No API keys are
        configured, so Refresh scores / Generate report / Research this
        company are disabled below.
      </p>
    </div>
  );
}

export default DemoModeBanner;
