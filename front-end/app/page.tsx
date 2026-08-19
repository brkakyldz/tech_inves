import { Hero } from "@/components/landing/Hero";
import { ScoreHighlights } from "@/components/landing/ScoreHighlights";
import { ReportPreview } from "@/components/landing/ReportPreview";
import { RunControlPanel } from "@/components/runs/RunControlPanel";

// ADR 0010 §1 / plan §6: the marketing composition is replaced by a control
// panel. Order: the three triggers (plus, inside the same panel, the
// last-run summary they naturally sit beside), then the largest score
// movements, then the most recent report -- ScoreHighlights and
// ReportPreview are reused as-is (plan §6 names them reuse candidates); the
// "largest movements" framing itself is what plan §8 Faz 7a still needs to
// add a run-over-run delta for, so this reuses the existing highlight
// selection rather than inventing a delta view ahead of that phase.
export default function Home() {
  return (
    <>
      <Hero />
      <RunControlPanel />
      <ScoreHighlights />
      <ReportPreview />
    </>
  );
}
