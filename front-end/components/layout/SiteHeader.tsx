import Link from "next/link";
import { Wordmark } from "@/components/brand/Wordmark";

export function SiteHeader() {
  return (
    <header className="border-b border-border">
      <div className="content-wrap flex items-center justify-between py-4">
        <Link href="/" aria-label="TechInves home">
          <Wordmark size="sm" />
        </Link>
        <nav className="flex items-center gap-6 text-sm text-muted-foreground">
          <Link href="/screener" className="hover:text-foreground">
            Screener
          </Link>
          <Link href="/reports" className="hover:text-foreground">
            Reports
          </Link>
          <Link href="/legal" className="hover:text-foreground">
            Methodology &amp; Legal
          </Link>
        </nav>
      </div>
    </header>
  );
}
