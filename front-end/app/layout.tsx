import type { Metadata, Viewport } from "next";
import "./globals.css";
import { fraunces, sourceSans, jetbrainsMono } from "./fonts";
import { SiteHeader } from "@/components/layout/SiteHeader";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { DisclaimerBanner } from "@/components/layout/DisclaimerBanner";
import { DemoModeBanner } from "@/components/layout/DemoModeBanner";

const SITE_TITLE = "TechInves — US Technology Sector Score Report";
const SITE_DESCRIPTION =
  "Deterministic financial scoring and sector reporting for US technology companies.";

// Same fallback pattern as lib/api/client.ts's API_BASE_URL -- a real
// deploy sets NEXT_PUBLIC_SITE_URL; local dev falls back to the dev server
// origin so relative og-image/icon URLs still resolve to something valid.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  openGraph: {
    type: "website",
    locale: "en_US",
    siteName: "TechInves",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
};

export const viewport: Viewport = {
  // Matches --background (oklch(1 0 0) = #ffffff) -- dark mode was removed
  // (design-system plan §7.1), so there is only ever one theme color.
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${sourceSans.variable} ${jetbrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* Runs before first paint, so the `.js`-scoped hidden default in
            globals.css (.stagger-item) only ever applies when JS is
            actually present to reveal it later -- no JS (or a crawler that
            never executes it) means the cards render visible from the very
            first paint, never permanently hidden. See StaggerItem.tsx. */}
        <script
          dangerouslySetInnerHTML={{
            __html: "document.documentElement.classList.add('js');",
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <DemoModeBanner />
        <DisclaimerBanner />
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
