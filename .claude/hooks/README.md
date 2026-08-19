# .claude/hooks

The four hooks that drive the `reports/` layer. **This is the canonical copy** —
they used to live in `~/.claude/hooks/` (machine-local, invisible to `git log`),
which is the problem recorded in
[`backlog/agentic-layer-is-unversioned-and-capture-is-lost.md`](../../reports/backlog/agentic-layer-is-unversioned-and-capture-is-lost.md)
item 1. Moved here 2026-08-16.

| Hook | Event | What it does |
|---|---|---|
| `subagent-report.mjs` | `SubagentStop` | If the agent wrote no report, reconstructs one from the transcript tail into `reports/agents/_auto/`. Final messages under `MIN_BODY` (1200 chars) are dropped — a status line is not a finding. |
| `stop-synthesis.mjs` | `Stop` | Writes `reports/synthesis/_PENDING.md` when agent reports are unsynthesized; removes it once a synthesis lands. |
| `session-start-context.mjs` | `SessionStart` | Injects open backlog, unsynthesized report count, and latest synthesis into context. |
| `precompact-dump.mjs` | `PreCompact` | Checkpoints session state into `reports/backlog/` before compaction. |
| `lib.mjs` | — | Shared helpers. `filesByMtime()` skips `README.md` and any `_`-prefixed name, which is why `_auto/` and `_PENDING.md` are invisible to the counters. |

All four are inert in a project without `reports/` (`findReportsRoot()` returns
null), so they are safe to carry anywhere.

Wired up in [`../settings.json`](../settings.json) via `$CLAUDE_PROJECT_DIR`.
They are **not** in `~/.claude/settings.json` any more — if they were in both,
every hook would fire twice.

`guard-bash.mjs` deliberately stayed in `~/.claude/`: it is a personal safety net
across all projects, not part of this repo's agentic layer.

## Tests

```bash
node .claude/hooks/tests/reports-hooks.test.mjs
```

Builds a throwaway project in the OS temp dir and drives each hook with a
synthetic transcript. The `reports/` skeleton is built inline rather than by
calling the `reports-architecture` skill's `scaffold.mjs` — a test for versioned
hooks must not depend on a machine-local skill. Keep `SKELETON` in that file in
sync with the skill's `DIRS` if the layout changes.

Run it before committing any change under this directory.
