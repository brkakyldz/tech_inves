/**
 * Thin fetch wrapper for the TechInves back-end API
 * (reports/research/BACKEND_IMPLEMENTATION_PLAN.md §9). Single place that knows the
 * API's base URL, applies the tag-based cache contract, and maps HTTP
 * errors -- every `lib/data/*` function goes through this.
 */

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 8000;

/**
 * R12: bounded fallback so a missed or failed on-demand revalidation call
 * (app/api/revalidate/route.ts) doesn't leave the cache stale forever.
 * `cache: "force-cache"` had no time floor at all -- it relied entirely on
 * the tag-based webhook firing. One hour is well inside how stale a report
 * page is tolerable being if that call is ever missed or fails. As of Faz
 * 5.4, the caller is `RunControlPanel` (`components/runs/RunControlPanel.tsx`),
 * which hits the route the moment a triggered run succeeds -- replacing the
 * deleted approve-report publish step this was originally wired to (ADR
 * 0010). This constant is now purely the fallback floor for a missed call,
 * not the primary invalidation path's only backstop.
 */
const FALLBACK_REVALIDATE_SECONDS = 60 * 60;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiNotFoundError extends ApiError {
  constructor(path: string) {
    super(404, `not found: ${path}`);
    this.name = "ApiNotFoundError";
  }
}

/**
 * True only for "the API answered nothing at all" -- `apiFetch` maps a
 * thrown `fetch` (connection refused, DNS failure, request timeout) to
 * `ApiError` with status 0, which is the one failure mode that carries no
 * HTTP response.
 *
 * This is what `lib/data/*` collection fetchers tolerate so that
 * `next build` works against a machine with no API running: ADR 0010 §7's
 * fresh keyless clone builds the site first and starts the API afterwards,
 * so a build that requires a live, seeded back-end is a build that clone can
 * never perform. A real HTTP status is deliberately NOT covered here -- a
 * 500 from a running API is a defect that must surface, not be rendered as
 * an empty page.
 */
export function isApiUnreachable(err: unknown): boolean {
  return err instanceof ApiError && err.status === 0;
}

type SearchParamValue = string | number | boolean | string[] | undefined;

interface ApiFetchOptions {
  searchParams?: Record<string, SearchParamValue>;
  /** Cache tags for on-demand revalidation via /api/revalidate. Scores never
   * change outside a weekly ingestion run, so there is no time-based TTL --
   * see BACKEND_IMPLEMENTATION_PLAN.md §6. */
  tags?: string[];
}

function buildUrl(path: string, searchParams?: ApiFetchOptions["searchParams"]): string {
  const url = new URL(path, API_BASE_URL);
  for (const [key, value] of Object.entries(searchParams ?? {})) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const url = buildUrl(path, options.searchParams);

  let res: Response;
  try {
    res = await fetch(url, {
      // R12: tag-based revalidation stays the primary invalidation path
      // (immediate, triggered on publish); `revalidate` is only the bounded
      // fallback floor, not the normal refresh mechanism -- data here
      // otherwise only changes via a weekly ingestion/publish event.
      next: { tags: options.tags ?? [], revalidate: FALLBACK_REVALIDATE_SECONDS },
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch (cause) {
    throw new ApiError(0, `request to ${url} failed: ${(cause as Error).message}`);
  }

  if (res.status === 404) {
    throw new ApiNotFoundError(path);
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${url} responded ${res.status}`);
  }
  return (await res.json()) as T;
}

