import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Methodology & Legal Disclaimer — TechInves",
  description:
    "A short summary of TechInves's scoring methodology and legal disclaimer.",
};

export default function LegalPage() {
  return (
    <section className="section-y">
      <div className="content-wrap prose-measure">
        <h1 className="font-serif text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          What the scores are based on, and what they don&apos;t mean
        </h1>
        <p className="text-dek mt-4 text-muted-foreground text-pretty">
          You can find here how the composite score is calculated, which
          bands it falls into, and what data it relies on — and why it
          isn&apos;t investment advice.
        </p>

        <div className="mt-10 space-y-8 text-sm leading-relaxed text-muted-foreground">
        <section>
          <h2 className="font-serif text-lg font-semibold text-foreground">
            Not investment advice
          </h2>
          <p className="mt-2 text-pretty">
            TechInves scores and reports are a screening and ranking tool
            based on publicly available financial data. They do not contain
            buy/sell/hold recommendations, price targets, or personalized
            investment advice. You should consult a licensed investment
            advisor for your investment decisions.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-lg font-semibold text-foreground">
            How is the score calculated?
          </h2>
          <p className="mt-2 text-pretty">
            Each company is first assigned to its own cohort (Software &amp;
            Internet, Hardware/Semiconductors &amp; Space, or IT Services
            &amp; Infrastructure). The composite score, on a 0-100 scale, is
            the weighted sum of four weighted categories&apos; (Profitability
            &amp; Quality, Growth, Valuation, Financial Health) within-cohort
            percentile ranking. Scores are produced entirely from financial
            API data; no web search or subjective commentary enters the
            score calculation.
          </p>
        </section>

        <section>
          <h2 className="font-serif text-lg font-semibold text-foreground">
            Score bands
          </h2>
          <ul className="mt-2 list-inside list-disc space-y-1">
            <li>80-100 — Strong</li>
            <li>65-79 — Good</li>
            <li>45-64 — Moderate</li>
            <li>30-44 — Weak</li>
            <li>0-29 — Very Weak</li>
          </ul>
        </section>

        <section>
          <h2 className="font-serif text-lg font-semibold text-foreground">
            Data source and update frequency
          </h2>
          <p className="mt-2 text-pretty">
            Scores are recalculated at each quarterly earnings-season close;
            price-dependent valuation metrics are updated on each run.
          </p>
        </section>
        </div>
      </div>
    </section>
  );
}
