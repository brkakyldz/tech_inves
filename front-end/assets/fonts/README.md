# assets/fonts

Static font binaries for Satori (`next/og` `ImageResponse`) — used only by
`app/opengraph-image.tsx`, `app/reports/[slug]/opengraph-image.tsx`, and
`app/companies/[ticker]/opengraph-image.tsx`. These are **not** the fonts
the live site renders with (`app/fonts.ts` loads the full variable families
via `next/font/google` for that) — Satori's render environment doesn't run
in a browser and can't fetch `next/font`'s CSS-in-JS output, so OG/icon
routes need real font *bytes* available at module scope via
`node:fs/promises`.

## Why WOFF, not TTF

Satori accepts `ttf`/`otf`/`woff` (not `woff2`). Google Fonts' `css2` API
serves `woff2` to modern user agents; requesting with an old Firefox 4
user-agent string gets `woff` instead — no `woff2`-to-`woff` conversion
step needed. (An even older IE6 user-agent returns EOT-wrapped data, not
raw TTF — verified by inspecting the magic bytes; `wOFF` is what these
files actually start with.)

## Why single-weight, subsetted files

The 500KB Satori bundle cap (JSX + CSS + fonts + images combined,
per Vercel's `@vercel/og` docs) rules out embedding a full variable font.
Each file here is **one static weight**, subsetted to the Latin + Turkish
glyph set these routes actually render (`text=` param on the `css2`
request): `A-Za-z0-9`, `ÇĞİÖŞÜçğıöşü`, and basic punctuation. Total for all
four files is well under 60KB.

| File | Family | Weight | Used for |
|---|---|---|---|
| `Fraunces-SemiBold.woff` | Fraunces | 600 | headlines, wordmark |
| `SourceSans3-Regular.woff` | Source Sans 3 | 400 | body/dek copy |
| `SourceSans3-SemiBold.woff` | Source Sans 3 | 600 | eyebrows, tickers |
| `JetBrainsMono-SemiBold.woff` | JetBrains Mono | 600 | score numerals |

## Regenerating

If the OG copy changes and needs a glyph not in the current subset (rare —
the subset already covers full a-z/A-Z, 0-9, and Turkish extended Latin),
refetch with the old-UA trick against Google's `css2` endpoint:

```sh
curl -s -A "Mozilla/5.0 (Windows NT 6.1; rv:2.0.1) Gecko/20100101 Firefox/4.0.1" \
  "https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400&text=<url-encoded-text>" \
  -o out.css
# then curl -L the url( ... ) inside out.css to a .woff file
```

Verify the result starts with the `wOFF` magic bytes before committing it.
