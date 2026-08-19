"use client";

/**
 * The control panel ADR 0010 §1 replaces the marketing landing page with
 * (`reports/decisions/0010-on-demand-personal-use-model.md` §1, plan §6).
 *
 * Three triggers, each independently invocable, backed by
 * `POST /v1/runs` / `GET /v1/runs` / `GET /v1/runs/{id}`
 * (`src/techinves/api/routers/runs.py`). This component owns:
 *
 * - cost legibility: the three actions differ by orders of magnitude in
 *   cost, so they are visually distinct rather than three identical
 *   buttons, and the two expensive ones require a second confirming click
 *   before they fire.
 * - disabled-with-reason button states, covering every refusal the endpoint
 *   can return (`RunRefused`, `MissingApiKey`, `RunNotReconciled`,
 *   `UnknownTicker`, `TickerRequired`) plus a plain network failure.
 * - live log polling via the `log_offset` cursor -- each tick asks only for
 *   the log text appended since the last poll, never the whole log again.
 * - picking a run back up on mount: `GET /v1/runs` is read once on load so
 *   that a run already in flight (started before this page was opened, or
 *   before a reload) is found and its polling resumes, rather than the UI
 *   presenting an idle button while work is actually happening server-side
 *   (ADR 0010 §3 -- closing the page never cancels a run).
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  getMeta,
  getRun,
  listRuns,
  triggerRun,
  RunRefusalError,
  type RunStatus,
  type RunSummary,
  type RunTriggerType,
} from "@/lib/api/runs";

const POLL_INTERVAL_MS = 2500;
const TRIGGER_TYPES: readonly RunTriggerType[] = ["scores", "report", "company"];

// Which cache tag(s) a trigger's data lands under (lib/api/client.ts's
// `tags` contract, mirrored in app/api/revalidate/route.ts's `VALID_TAGS`).
// `scores` only ever calls `run_scores` (src/techinves/runs/work.py), which
// writes scores and nothing else; `report` and `company` both go through
// `_run_pipeline` -> the report pipeline, which writes reports only -- see
// `run_report`/`run_company` in the same file. Neither `report` nor
// `company` touches the scores table.
// `meta` rides along on every trigger: a run that reached `succeeded`
// proves the keys that run needed are present, so a `mode: "demo"` snapshot
// cached under that tag is now known to be wrong. Without this the
// server-rendered DemoModeBanner could keep asserting the triggers are
// disabled for up to FALLBACK_REVALIDATE_SECONDS while this panel -- which
// reads the same `/v1/meta` with `cache: "no-store"` -- showed them
// enabled. `/v1/meta` also carries `latestRunId`/`lastIngestedAt`, which a
// finished run of any type moves.
const REVALIDATE_TAGS_FOR_TRIGGER: Record<RunTriggerType, readonly string[]> = {
  scores: ["scores", "meta"],
  report: ["reports", "meta"],
  company: ["reports", "meta"],
};

// Best-effort cache bust the moment a run's data actually lands, so the
// reader isn't staring at a page that's stale for up to
// FALLBACK_REVALIDATE_SECONDS (lib/api/client.ts) after a run they just
// watched finish. This is the control panel picking up the caller role
// `/api/revalidate` lost when Faz 1 deleted the publish step it used to be
// wired to (reports/backlog/verifier-banner-followups.md item 3) -- the
// route itself is unchanged apart from learning the `meta` tag.
function revalidateAfterRun(type: RunTriggerType) {
  for (const tag of REVALIDATE_TAGS_FOR_TRIGGER[type]) {
    fetch(`/api/revalidate?tag=${tag}`, { method: "POST" }).catch(() => {
      // Non-fatal: the FALLBACK_REVALIDATE_SECONDS floor in lib/api/client.ts
      // still bounds how stale the page can get if this call is lost.
    });
  }
}

const TRIGGER_META: Record<
  RunTriggerType,
  {
    label: string;
    description: string;
    costLabel: string;
    costTone: "cheap" | "expensive";
    needsConfirm: boolean;
  }
> = {
  scores: {
    label: "Refresh scores",
    description:
      "Re-pulls fundamentals for the whole watchlist and recomputes every score. Deterministic, no LLM calls.",
    costLabel: "Fast · no LLM cost",
    costTone: "cheap",
    needsConfirm: false,
  },
  report: {
    label: "Generate report",
    description:
      "Runs the full research, synthesis and verification chain across the whole watchlist.",
    costLabel: "Slow · several minutes · calls paid LLM & search APIs",
    costTone: "expensive",
    needsConfirm: true,
  },
  company: {
    label: "Research this company",
    description: "Runs the same research chain narrowed to one ticker.",
    costLabel: "Targeted · calls paid LLM & search APIs",
    costTone: "expensive",
    needsConfirm: true,
  },
};

interface Refusal {
  code: string;
  message: string;
  extra: Record<string, unknown>;
}

interface TriggerState {
  lastRun: RunSummary | null;
  activeRunId: string | null;
  activeStatus: RunStatus | null;
  log: string;
  logOffset: number;
  refusal: Refusal | null;
  busy: boolean;
  /** Set on a `MissingApiKey` refusal -- true until the page reloads, since
   * an absent environment variable cannot become present mid-session. */
  keyMissing: boolean;
}

const EMPTY_TRIGGER_STATE: TriggerState = {
  lastRun: null,
  activeRunId: null,
  activeStatus: null,
  log: "",
  logOffset: 0,
  refusal: null,
  busy: false,
  keyMissing: false,
};

type PanelState = Record<RunTriggerType, TriggerState>;

function initialPanelState(): PanelState {
  return {
    scores: { ...EMPTY_TRIGGER_STATE },
    report: { ...EMPTY_TRIGGER_STATE },
    company: { ...EMPTY_TRIGGER_STATE },
  };
}

function refusalMessage(err: RunRefusalError): string {
  switch (err.code) {
    case "MissingApiKey":
      return `Disabled — missing ${String(err.extra.missing_key ?? "a required API key")} in the environment.`;
    case "RunRefused":
      return `Already running as run ${String(err.extra.active_run_id ?? "an active run")} — this will re-enable once it finishes.`;
    case "RunNotReconciled":
      return "The server finished restarting a moment ago and is still reconciling — try again shortly.";
    case "UnknownTicker":
      return `"${String(err.extra.ticker ?? "")}" is not on the watchlist.`;
    case "TickerRequired":
      return "Enter a ticker first.";
    case "UnexpectedTicker":
      return "This trigger does not take a ticker.";
    case "NetworkError":
      return err.message;
    case "NotFound":
      return "That run no longer exists.";
    default:
      return err.message;
  }
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
}

function formatCost(usd: number): string {
  if (!usd) return "$0.00";
  return `$${usd.toFixed(2)}`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  // Explicit locale, not the browser's -- every other string on this panel
  // is English by instruction, and an inherited locale would silently
  // render Turkish month names on a machine set to tr-TR.
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

function StatusBadge({ status }: { status: RunStatus }) {
  const variant =
    status === "succeeded" ? "secondary" : status === "failed" ? "destructive" : "outline";
  return (
    <Badge variant={variant} className={status === "running" ? "animate-pulse" : undefined}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

export function RunControlPanel() {
  const [state, setState] = useState<PanelState>(initialPanelState);
  const [ticker, setTicker] = useState("");
  const [confirming, setConfirming] = useState<RunTriggerType | null>(null);
  const confirmTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stateRef = useRef(state);
  const mountedRef = useRef(true);
  /** Run ids with a `getRun` already awaiting a response -- see the poll
   * effect below. A ref, not state: it must be read and written inside one
   * interval tick without scheduling a render. */
  const pollsInFlightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Faz 6 (ADR 0010 §7): know which triggers are missing a key up front,
  // on mount, instead of discovering it reactively per click. `getMeta()`
  // reads the same `_missing_required_key` check the endpoint refuses
  // with (`techinves.runs.keys`, shared source of truth), so the reason
  // shown here is never out of sync with what a click would actually get
  // refused for.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { missingKeys } = await getMeta();
        if (cancelled) return;
        setState((prev) => {
          const next = { ...prev };
          for (const type of TRIGGER_TYPES) {
            const missingKey = missingKeys[type];
            if (!missingKey) continue;
            next[type] = {
              ...next[type],
              keyMissing: true,
              refusal: {
                code: "MissingApiKey",
                message: `Disabled — missing ${missingKey} in the environment.`,
                extra: { missing_key: missingKey },
              },
            };
          }
          return next;
        });
      } catch {
        // Non-fatal: a trigger with an actually-missing key still gets
        // caught by the endpoint's own refusal on click.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Pick up in-flight or most-recent runs on mount / reopen -- a run
  // triggered before this page was opened (or before a reload) is not lost;
  // its polling resumes here rather than the UI showing an idle button
  // while the server is actually mid-run (ADR 0010 §3).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { items } = await listRuns(50);
        if (cancelled) return;
        setState((prev) => {
          const next = { ...prev };
          for (const type of TRIGGER_TYPES) {
            const mostRecent = items.find((r) => r.triggerType === type);
            if (!mostRecent) continue;
            const isActive = mostRecent.status === "queued" || mostRecent.status === "running";
            next[type] = {
              ...next[type],
              lastRun: isActive ? next[type].lastRun : mostRecent,
              activeRunId: isActive ? mostRecent.runId : null,
              activeStatus: isActive ? mostRecent.status : null,
              log: "",
              logOffset: 0,
            };
          }
          return next;
        });
      } catch {
        // Non-fatal: the panel still works for new triggers even if the
        // history fetch fails (e.g. the API isn't reachable yet).
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const anyActive = TRIGGER_TYPES.some((type) => state[type].activeRunId !== null);

  // One interval drives every active run's polling. It only exists while
  // something is actually in flight -- once the last active run reaches a
  // terminal status, `anyActive` flips false, the effect's cleanup clears
  // the interval, and no timer is left running for a page left open idle.
  useEffect(() => {
    if (!anyActive) return;
    const interval = setInterval(async () => {
      const current = stateRef.current;
      for (const type of TRIGGER_TYPES) {
        const s = current[type];
        const runId = s.activeRunId;
        if (!runId) continue;
        // In-flight guard. Without it, a `getRun` slower than
        // POLL_INTERVAL_MS lets the next tick read the same, not-yet-advanced
        // cursor from `stateRef` and re-request the identical tail -- and
        // both updaters then append it, since the `runId` check below
        // matches for both. The log-offset cursor is the state that makes
        // this duplication silent, so the fix guards the request, not the
        // render.
        if (pollsInFlightRef.current.has(runId)) continue;
        const at = s.logOffset;
        pollsInFlightRef.current.add(runId);
        try {
          const detail = await getRun(runId, at);
          if (!mountedRef.current) return;
          if (detail.status === "succeeded") revalidateAfterRun(type);
          setState((prev) => {
            const cur = prev[type];
            if (cur.activeRunId !== detail.runId) return prev; // superseded mid-flight
            // Second line of defence on the same invariant: this chunk was
            // requested from `at`, so it can only be appended to a buffer
            // still sitting at `at`.
            if (cur.logOffset !== at) return prev;
            const terminal = detail.status === "succeeded" || detail.status === "failed";
            return {
              ...prev,
              [type]: {
                ...cur,
                log: cur.log + detail.log,
                logOffset: detail.logOffset,
                activeStatus: detail.status,
                activeRunId: terminal ? null : cur.activeRunId,
                lastRun: terminal ? detail : cur.lastRun,
              },
            };
          });
        } catch {
          // A missed poll tick retries from the same offset next tick.
        } finally {
          // Runs on the unmount `return` above too, so a poll can never
          // leave its run id stuck in the set.
          pollsInFlightRef.current.delete(runId);
        }
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [anyActive]);

  async function handleTrigger(type: RunTriggerType) {
    const meta = TRIGGER_META[type];

    // Cheap, certain, client-known checks go before the confirm gate: a
    // user should never be asked to confirm spending money on a request
    // that a plain empty text field already dooms.
    const tickerArg = type === "company" ? ticker.trim().toUpperCase() : undefined;
    if (type === "company" && !tickerArg) {
      setState((prev) => ({
        ...prev,
        company: {
          ...prev.company,
          refusal: { code: "TickerRequired", message: "Enter a ticker first.", extra: {} },
        },
      }));
      return;
    }

    if (meta.needsConfirm && confirming !== type) {
      setConfirming(type);
      if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current);
      confirmTimeoutRef.current = setTimeout(() => setConfirming(null), 8000);
      return;
    }
    setConfirming(null);
    if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current);

    setState((prev) => ({ ...prev, [type]: { ...prev[type], busy: true, refusal: null } }));

    try {
      const result = await triggerRun(type, tickerArg);
      setState((prev) => ({
        ...prev,
        [type]: {
          ...prev[type],
          busy: false,
          activeRunId: result.runId,
          activeStatus: result.status,
          log: "",
          logOffset: 0,
          refusal: null,
        },
      }));
    } catch (err) {
      if (err instanceof RunRefusalError) {
        const heldRunId =
          err.code === "RunRefused" && typeof err.extra.active_run_id === "string"
            ? err.extra.active_run_id
            : null;
        setState((prev) => {
          const cur = prev[type];
          // Adopting a *different* run means the buffered log and the
          // cursor belong to the previous one. The cursor is the damaging
          // half: `GET /v1/runs/{id}?log_offset=<previous run's length>`
          // returns an empty tail (src/techinves/api/routers/runs.py) and
          // reports the adopted run's real length back, so its opening
          // lines would be skipped for good -- while the finished run's
          // text stayed on screen labelled as the live one. Reset both,
          // exactly as the trigger-success and mount-resume paths do.
          const adopting = heldRunId !== null && heldRunId !== cur.activeRunId;
          return {
            ...prev,
            [type]: {
              ...cur,
              busy: false,
              refusal: { code: err.code, message: refusalMessage(err), extra: err.extra },
              keyMissing: cur.keyMissing || err.code === "MissingApiKey",
              // A refusal naming another run of the same type as the lock
              // holder is itself worth tracking: polling it means this
              // button re-enables the moment that run finishes, with no
              // extra click needed.
              activeRunId: heldRunId ?? cur.activeRunId,
              activeStatus: heldRunId ? "running" : cur.activeStatus,
              log: adopting ? "" : cur.log,
              logOffset: adopting ? 0 : cur.logOffset,
            },
          };
        });
      } else {
        setState((prev) => ({
          ...prev,
          [type]: {
            ...prev[type],
            busy: false,
            refusal: { code: "UnknownError", message: (err as Error).message, extra: {} },
          },
        }));
      }
    }
  }

  return (
    <section className="section-y">
      <div className="content-wrap">
        <div className="prose-measure">
          <h2 className="font-serif text-3xl font-semibold tracking-tight">Run the pipeline</h2>
          <p className="mt-3 text-muted-foreground text-pretty">
            Every run happens on this machine, on demand — nothing is scheduled. Runs continue in
            the background even if you close this page; reopening it picks up wherever a run left
            off.
          </p>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-4 lg:grid-cols-3">
          {TRIGGER_TYPES.map((type) => (
            <TriggerCard
              key={type}
              type={type}
              state={state[type]}
              confirming={confirming === type}
              ticker={ticker}
              onTickerChange={setTicker}
              onTrigger={() => handleTrigger(type)}
              onCancelConfirm={() => {
                setConfirming(null);
                if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current);
              }}
            />
          ))}
        </div>

        <div className="mt-10">
          <h3 className="font-serif text-xl font-semibold tracking-tight">Last run, per trigger</h3>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            {TRIGGER_TYPES.map((type) => (
              <LastRunSummaryCard key={type} type={type} state={state[type]} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function TriggerCard({
  type,
  state,
  confirming,
  ticker,
  onTickerChange,
  onTrigger,
  onCancelConfirm,
}: {
  type: RunTriggerType;
  state: TriggerState;
  confirming: boolean;
  ticker: string;
  onTickerChange: (value: string) => void;
  onTrigger: () => void;
  onCancelConfirm: () => void;
}) {
  const meta = TRIGGER_META[type];
  const disabled = state.busy || state.activeRunId !== null || state.keyMissing;

  return (
    <Card className={cn(meta.costTone === "expensive" && "ring-amber-500/30")}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>{meta.label}</CardTitle>
          {state.activeRunId && <StatusBadge status={state.activeStatus ?? "queued"} />}
        </div>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Badge
          variant={meta.costTone === "expensive" ? "destructive" : "secondary"}
          className="font-normal"
        >
          {meta.costLabel}
        </Badge>

        {type === "company" && (
          <Input
            placeholder="Ticker, e.g. NVDA"
            value={ticker}
            onChange={(e) => onTickerChange(e.target.value)}
            disabled={disabled}
            className="font-mono uppercase"
          />
        )}

        <div className="flex items-center gap-2">
          <Button
            onClick={onTrigger}
            disabled={disabled}
            variant={confirming ? "destructive" : "default"}
            className="w-full"
          >
            {state.busy
              ? "Starting…"
              : confirming
                ? "Confirm — this will cost time & money"
                : meta.label}
          </Button>
          {confirming && (
            <Button variant="ghost" onClick={onCancelConfirm}>
              Cancel
            </Button>
          )}
        </div>

        {state.refusal && (
          <p className="text-xs text-destructive text-pretty" role="alert">
            {state.refusal.message}
          </p>
        )}

        {state.activeRunId && (
          <RunLogView status={state.activeStatus ?? "queued"} log={state.log} />
        )}
      </CardContent>
    </Card>
  );
}

function RunLogView({ status, log }: { status: RunStatus; log: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Live log
      </p>
      <pre className="mt-1 max-h-48 overflow-y-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap text-muted-foreground">
        {log || (status === "queued" ? "Queued — waiting to start…" : "Running — waiting for output…")}
      </pre>
    </div>
  );
}

function LastRunSummaryCard({ type, state }: { type: RunTriggerType; state: TriggerState }) {
  const run = state.lastRun;
  const meta = TRIGGER_META[type];

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="text-sm">{meta.label}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5 text-sm">
        {!run ? (
          <p className="text-muted-foreground">No runs yet.</p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <StatusBadge status={run.status} />
              {run.verdict && (
                <Badge variant="outline" className="font-mono text-xs">
                  {run.verdict}
                </Badge>
              )}
            </div>
            <p className="text-muted-foreground">{formatTimestamp(run.finishedAt ?? run.createdAt)}</p>
            <p className="text-muted-foreground">
              {formatDuration(run.durationSeconds)}
              {run.totalCostUsd > 0 && ` · ${formatCost(run.totalCostUsd)}`}
            </p>
            {run.error && (
              <p className="text-xs text-destructive text-pretty">{run.error}</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
