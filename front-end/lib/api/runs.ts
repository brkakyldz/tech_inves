/**
 * Client-side fetch layer for `/v1/runs` (Faz 5 control panel,
 * `reports/plans/2026-08-18_on-demand-transformation.md` §6, ADR 0010 §1-3).
 *
 * Deliberately separate from `lib/api/client.ts`: that wrapper is built for
 * server components reading tag-cached, rarely-changing data
 * (`next: { tags, revalidate }`). Runs are triggered and polled from a
 * client component (a browser `fetch`, not Next's server fetch), the
 * response body's error `detail` is a structured `{code, message, ...}` the
 * UI needs to render verbatim rather than a generic HTTP failure, and every
 * request must bypass the cache (`cache: "no-store"`) -- a stale trigger
 * response or a stale log tail would silently break the polling contract.
 *
 * `NEXT_PUBLIC_API_BASE_URL` is unset by default; every consumer of this
 * module runs in the browser, where `API_BASE_URL` (server-only) is not
 * available, and defaults to the local dev API on `:8000`.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Same ceiling `apiFetch` puts on the server-side reads
 * (`lib/api/client.ts`'s `DEFAULT_TIMEOUT_MS`). Without it a hung request
 * has no upper bound at all, which matters most for the 2.5 s log poll in
 * `RunControlPanel`: a request that never settles would hold that run's
 * in-flight slot open indefinitely and stall its live log, rather than
 * failing and being retried from the same cursor on the next tick.
 */
const REQUEST_TIMEOUT_MS = 8000;

export type RunTriggerType = "scores" | "report" | "company";

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface RunSummary {
  runId: string;
  triggerType: RunTriggerType;
  ticker: string | null;
  status: RunStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
  verdict: string | null;
  durationSeconds: number;
  findingsCount: number;
  failureCount: number;
  totalTokens: number;
  totalCostUsd: number;
}

export interface RunDetail extends RunSummary {
  log: string;
  logOffset: number;
}

export interface RunListResponse {
  items: RunSummary[];
  page: number;
  pageSize: number;
  total: number;
}

export interface RunTriggerResult {
  runId: string;
  triggerType: RunTriggerType;
  ticker: string | null;
  status: RunStatus;
}

/**
 * A refusal from `POST /v1/runs` or an unknown-run 404 from
 * `GET /v1/runs/{id}`, carrying `src/techinves/api/routers/runs.py`'s
 * machine-readable `code` and whatever extra fields that refusal names
 * (`missing_key`, `active_run_id`, `ticker`, ...) so the UI can render the
 * exact reason rather than a generic failure message. Note these extra
 * fields stay snake_case on the wire: the refusal `detail` is a plain
 * Python dict passed to `HTTPException`, not a `CamelModel`, so
 * `alias_generator=to_camel` never reaches it.
 */
export class RunRefusalError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "RunRefusalError";
  }
}

async function runsFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
      signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (cause) {
    throw new RunRefusalError(
      0,
      "NetworkError",
      `could not reach the API at ${API_BASE_URL}: ${(cause as Error).message}`,
    );
  }

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const { code, message, ...extra } = detail as {
        code?: string;
        message?: string;
        [key: string]: unknown;
      };
      throw new RunRefusalError(
        res.status,
        code ?? "UnknownError",
        message ?? `request failed with status ${res.status}`,
        extra,
      );
    }
    const message = typeof detail === "string" ? detail : `request failed with status ${res.status}`;
    throw new RunRefusalError(res.status, res.status === 404 ? "NotFound" : "UnknownError", message);
  }

  return body as T;
}

/** `POST /v1/runs`. `ticker` is only meaningful for the `company` trigger. */
export async function triggerRun(
  triggerType: RunTriggerType,
  ticker?: string,
): Promise<RunTriggerResult> {
  return runsFetch<RunTriggerResult>("/v1/runs", {
    method: "POST",
    body: JSON.stringify({ triggerType, ticker: ticker ?? null }),
  });
}

/** `GET /v1/runs` -- most recent runs first (`createdAt DESC`). */
export async function listRuns(pageSize = 20): Promise<RunListResponse> {
  return runsFetch<RunListResponse>(`/v1/runs?pageSize=${pageSize}`);
}

/**
 * `GET /v1/runs/{id}` with the log-offset cursor protocol: pass the
 * previous response's `logOffset` back in to receive only the log text
 * appended since then. `logOffset` defaults to 0 (the whole log so far).
 *
 * Note the wire query parameter is `log_offset`, not camelCased -- it is a
 * plain FastAPI `Query(...)` parameter (not a `CamelModel` field), so the
 * `alias_generator=to_camel` that applies to request/response bodies
 * elsewhere in this API does not reach it.
 */
export async function getRun(runId: string, logOffset = 0): Promise<RunDetail> {
  return runsFetch<RunDetail>(`/v1/runs/${encodeURIComponent(runId)}?log_offset=${logOffset}`);
}

/**
 * `GET /v1/meta`'s `mode`/`missingKeys` fields (Faz 6, ADR 0010 §7).
 *
 * `lib/data/meta.ts` already reads this server-side for the site-wide
 * `DemoModeBanner`; this is the client-side counterpart, fetched from
 * `RunControlPanel` on mount so a trigger known to be missing its key shows
 * disabled-with-reason immediately, rather than only after the visitor
 * clicks it and receives a `MissingApiKey` refusal. Two independent reads
 * of the same endpoint from two different rendering contexts (server vs.
 * browser `fetch`), same as the rest of this module's split from
 * `lib/api/client.ts` (see the file header).
 */
export interface AppMeta {
  mode: "demo" | "live";
  missingKeys: Partial<Record<RunTriggerType, string>>;
}

export async function getMeta(): Promise<AppMeta> {
  return runsFetch<AppMeta>("/v1/meta");
}
