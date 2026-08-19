export function DisclaimerBanner() {
  return (
    <div className="border-b border-border bg-muted/50">
      <p className="content-wrap py-2 text-center text-xs text-muted-foreground">
        TechInves scores and reports are a screening/analysis tool; not
        investment advice. They contain no buy/sell/hold recommendation.{" "}
        <a href="/legal" className="underline underline-offset-2 hover:text-foreground">
          Detailed explanation
        </a>
      </p>
    </div>
  );
}
