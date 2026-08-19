/**
 * `GET /v1/meta` -- Faz 6 (`reports/plans/2026-08-18_on-demand-transformation.md`
 * §7, ADR 0010 §7-8). This is the one field the run-trigger client
 * (`lib/api/runs.ts`) does not cover: `mode`/`missingKeys` are read once on
 * page load through Next's server-fetch layer (`lib/api/client.ts`), the same
 * way `lib/data/reports.ts` reads reports, so a visitor's very first paint
 * already knows which triggers are disabled and the demo banner can render
 * without waiting on a client round trip.
 */
import { apiFetch } from "@/lib/api/client";

export type AppMode = "demo" | "live";

export interface AppMeta {
  mode: AppMode;
  missingKeys: Record<string, string>;
}

interface MetaResponse extends AppMeta {
  cohorts: unknown[];
  bands: string[];
  latestRunId: string | null;
  lastIngestedAt: string | null;
}

/**
 * Never throws: a visitor should still see the rest of the site if the API
 * is briefly unreachable, so an unreadable meta response is treated as
 * "unknown" rather than failing the page. `DemoModeBanner` renders nothing
 * for `null` -- it only ever asserts demo state positively, never guesses.
 */
export async function getAppMeta(): Promise<AppMeta | null> {
  try {
    const res = await apiFetch<MetaResponse>("/v1/meta", { tags: ["meta"] });
    return { mode: res.mode, missingKeys: res.missingKeys };
  } catch {
    return null;
  }
}
