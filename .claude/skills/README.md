# .claude/skills

The six skills the `CLAUDE.md` reporting contract depends on. **This is the
canonical copy** — they used to live only in `~/.claude/skills/` (machine-local,
invisible to `git log`), so a fresh clone could read the contract but not
execute it. That is item 1 of
[`backlog/agentic-layer-is-unversioned-and-capture-is-lost.md`](../../reports/backlog/agentic-layer-is-unversioned-and-capture-is-lost.md).
Moved here 2026-08-16, following the hooks in [`../hooks/`](../hooks/README.md).

| Skill | Writes to | Role in the contract |
|---|---|---|
| `agent-report` | `reports/agents/` | The single frontmatter contract every report obeys. Synthesis, audits and `session-start-context.mjs` all parse it. |
| `report-synthesis` | `reports/synthesis/`, `reports/backlog/` | Reduces unsynthesized reports to one document; never edits or deletes an agent report. |
| `adr` | `reports/decisions/` | Records expensive-to-reverse decisions and supersedes contradicting ADRs. |
| `audit-window` | `reports/audits/{48h,72h}/` | Groups by time rather than topic; read-only over its sources. |
| `reports-architecture` | a fresh project | Scaffolds the whole skeleton via `scripts/scaffold.mjs`. The only skill aimed at *other* repos. |
| `review-berke` | nothing | `/review-berke`; delegates to the `code-reviewer-berke` subagent in [`../agents/`](../agents/), which is why that agent is versioned alongside it. |

## Precedence

Project skills shadow personal ones of the same name. If a stale copy is still
sitting in `~/.claude/skills/`, the repo copy is the one that runs here — but
delete the personal copy anyway, because editing the wrong file and seeing no
effect is the failure mode this move exists to prevent.

## Two things that were fixed on the way in

- `reports-architecture` documented `scaffold.mjs` under an absolute
  `C:/Users/...` path; it now uses `$CLAUDE_PROJECT_DIR`.
- `agent-report` specified `YYYY-MM-DD_<agent>_HH-MM-SSZ.md` filenames, which is
  exactly the timestamp-only form `CLAUDE.md`'s retention rules reject. It now
  specifies the topic-named form the rest of the contract assumes.

## No tests

Unlike the hooks, these are prompt files with no executable surface — the only
check is that the contract they describe matches `CLAUDE.md` and the hooks'
parsing. When changing frontmatter field names in `agent-report`, check
[`../hooks/lib.mjs`](../hooks/lib.mjs) and run the hook tests.
