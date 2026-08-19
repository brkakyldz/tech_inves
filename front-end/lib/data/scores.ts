/**
 * Real back-end-API-backed replacement for the former lib/data/mock-scores.ts.
 * Signatures and return types are unchanged (BACKEND_IMPLEMENTATION_PLAN.md
 * §3.4 / §9) -- every caller (screener, company detail page, landing page
 * score highlights) is untouched by this swap.
 */
import { apiFetch, ApiNotFoundError, isApiUnreachable } from "@/lib/api/client";
import type { ScoreBlock, ScoreHistoryPoint } from "./types";

const SCORES_TAG = "scores";

interface CompanyListResponse {
  items: ScoreBlock[];
  page: number;
  pageSize: number;
  total: number;
  runId: string | null;
}

/**
 * The two collection reads below are the ones `next build` runs before any
 * API exists: the landing page, the screener, and
 * `generateStaticParams` (`app/companies/[ticker]/page.tsx`) all go through
 * them. An unreachable API therefore degrades to an empty collection --
 * pages render their empty state and `generateStaticParams` produces no
 * routes, leaving every ticker to `dynamicParams` once the API is up. Only
 * `isApiUnreachable` (status 0, no HTTP response at all) is swallowed: a
 * 4xx/5xx from a running API still throws and reaches `app/error.tsx`.
 *
 * The single-record reads (`getScoreByTicker`, `getScoreHistory`) are
 * deliberately left throwing. They never run at build time -- with no
 * static params there is nothing to prerender -- and at request time
 * "the API is down" must not be rendered as "this company does not exist".
 */
export async function getScoreHighlights(): Promise<ScoreBlock[]> {
  try {
    return await apiFetch<ScoreBlock[]>("/v1/scores/highlights", { tags: [SCORES_TAG] });
  } catch (err) {
    if (isApiUnreachable(err)) return [];
    throw err;
  }
}

export async function getAllScores(): Promise<ScoreBlock[]> {
  try {
    const res = await apiFetch<CompanyListResponse>("/v1/companies", {
      searchParams: { pageSize: 200 },
      tags: [SCORES_TAG],
    });
    return res.items;
  } catch (err) {
    if (isApiUnreachable(err)) return [];
    throw err;
  }
}

export async function getScoreByTicker(ticker: string): Promise<ScoreBlock | undefined> {
  try {
    return await apiFetch<ScoreBlock>(`/v1/companies/${encodeURIComponent(ticker)}`, {
      tags: [SCORES_TAG],
    });
  } catch (err) {
    if (err instanceof ApiNotFoundError) return undefined;
    throw err;
  }
}

export async function getScoreHistory(ticker: string): Promise<ScoreHistoryPoint[]> {
  try {
    return await apiFetch<ScoreHistoryPoint[]>(`/v1/companies/${encodeURIComponent(ticker)}/history`, {
      tags: [SCORES_TAG],
    });
  } catch (err) {
    if (err instanceof ApiNotFoundError) return [];
    throw err;
  }
}
