import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-border">
      <div className="content-wrap py-8 text-sm text-muted-foreground">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p>&copy; {new Date().getFullYear()} TechInves</p>
          <div className="flex gap-6">
            <Link href="/screener" className="hover:text-foreground">
              Screener
            </Link>
            <Link href="/reports" className="hover:text-foreground">
              Reports
            </Link>
            <Link href="/legal" className="hover:text-foreground">
              Methodology &amp; Legal Disclaimer
            </Link>
          </div>
        </div>
        <p className="mt-4 max-w-3xl text-xs">
          TechInves scores are a deterministic screening tool based solely on
          publicly available financial data. Not investment advice, a price
          target, or a portfolio recommendation.
        </p>
      </div>
    </footer>
  );
}
