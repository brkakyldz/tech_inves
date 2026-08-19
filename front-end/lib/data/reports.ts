/**
 * Real back-end-API-backed replacement for the former lib/data/mock-reports.ts.
 * Signatures and return types are unchanged (REPORTS_AND_PIPELINE_INTEGRATION_PLAN.md
 * §4/§10 Faz 4) -- every caller (reports archive/detail pages, landing page
 * report preview) is untouched by this swap except for the `await` this
 * async swap requires.
 */
import { apiFetch, ApiNotFoundError, isApiUnreachable } from "@/lib/api/client";
import type { ReportDetail, ReportSummary } from "./types";

const REPORTS_TAG = "reports";

interface ReportListResponse {
  items: ReportSummary[];
  page: number;
  pageSize: number;
  total: number;
}

/**
 * `getLatestReport` and `getAllReports` are read by `next build` before any
 * API exists (the landing page's `ReportPreview`, the archive page, and
 * `generateStaticParams` in `app/reports/[slug]/page.tsx`), so an
 * unreachable API degrades to "no reports" rather than failing the build --
 * see the matching note in `lib/data/scores.ts`. Only status 0 (no HTTP
 * response at all) is swallowed; a 4xx/5xx from a running API still throws.
 *
 * `getReportBySlug` is left throwing for the same reason
 * `getScoreByTicker` is: it only runs per request, and an unreachable API
 * must not be presented to a reader as a deleted report.
 */
export async function getLatestReport(): Promise<ReportSummary | undefined> {
  try {
    return await apiFetch<ReportSummary>("/v1/reports/latest", { tags: [REPORTS_TAG] });
  } catch (err) {
    if (err instanceof ApiNotFoundError || isApiUnreachable(err)) return undefined;
    throw err;
  }
}

export async function getAllReports(): Promise<ReportSummary[]> {
  try {
    const res = await apiFetch<ReportListResponse>("/v1/reports", {
      searchParams: { pageSize: 100 },
      tags: [REPORTS_TAG],
    });
    return res.items;
  } catch (err) {
    if (isApiUnreachable(err)) return [];
    throw err;
  }
}

export async function getReportBySlug(slug: string): Promise<ReportDetail | undefined> {
  try {
    return await apiFetch<ReportDetail>(`/v1/reports/${encodeURIComponent(slug)}`, {
      tags: [REPORTS_TAG],
    });
  } catch (err) {
    if (err instanceof ApiNotFoundError) return undefined;
    throw err;
  }
}
