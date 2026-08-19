# TechInves

On-demand sector report production for the US technology sector + company financials
tracking. Self-hosted, single-user, run locally with the user's own keys — nothing is
scheduled and nothing is delivered to anyone (the reasoning is recorded locally
in ADR 0010 under `reports/decisions/`, which is untracked).

---

## Agent rules

- Model: `sonnet` (source gathering, summarization, search, mechanical work) / `opus`
  (synthesis, comparison, work requiring judgment) / `haiku` (simple bulk filtering).
  Rule: if the output will be consumed by a human and involves judgment, use Opus; if it's
  agent input or purely mechanical, use Sonnet. The `model` is explicitly specified on every
  `Agent` call.
- Agent type: `general-purpose` (web research), `Explore` (code search),
  `Plan` (implementation planning).
- Independent research tasks are spawned in parallel, each given a single topic/narrow
  scope, with the expected output format specified. No more than 4 agents are opened at
  once. If a single WebSearch/WebFetch is sufficient, no agent is opened.
- The agent producing a report is given the target file path; output is written directly
  to that file (Write/Edit). The final message does not repeat the report — a 150-200 word
  summary + 3-5 key findings + file path. Synthesizing multiple reports is delegated to a
  separate (usually opus) agent. On updates, the file is not rewritten from scratch — only
  the relevant section is Edited. Before starting new research, check whether it already
  exists under `reports/`.
- This applies to every agent working in this repo — sub-agents and the main
  session alike, no distinction.
- Each agent (main session or sub-agent) commits the files it wrote itself
  when it finishes a unit of work (files are added individually, no `git add -A`).
  No agent touches another agent's file. Parallel agents are never given
  overlapping target files.
- When an agent finishes its work, it commits the files it itself wrote, staged
  individually by name. Exception: if committing would touch, overwrite, or otherwise
  interfere with another (possibly still-running) agent's files or in-progress work — e.g.
  a conflicting working-tree state, a file outside its own assigned scope, or a commit that
  would land in the middle of another agent's uncommitted changes — the agent skips the
  commit and reports the conflict in its final message instead of forcing it through. This
  preserves the parallel-agent isolation guarantee above.
- Before committing, the agent runs the relevant tests/checks for the code it touched
  (e.g. the project's test suite, lint, build/typecheck) and confirms they pass. If no
  relevant automated check exists for the change, this is noted in the final message
  instead of skipped silently. Failing checks are fixed before committing, not bypassed.
- Since agents run in parallel and share one working directory, they follow the
  coordination protocol in [AGENT_COORDINATION.md](AGENT_COORDINATION.md): register
  scope in `.claude/active-agents.md` before starting, and before committing check
  `git status`/`git diff --name-only` to confirm only your own files are staged (never
  `git add -A`) and that no other agent's in-flight work is being swept in.

## Reporting contract

- Every delegated task writes its findings to `reports/agents/` when it finishes
  (format: the `agent-report` skill).
- Once several agent reports accumulate, `report-synthesis` reduces them to a
  single summary under `reports/synthesis/`.
- Technical decisions that are expensive to reverse are recorded as ADRs under
  `reports/decisions/`; a new decision reads these first and must not silently
  contradict them.
- Unfinished work stays open under `reports/backlog/`; delete the file when it closes.

## Report retention

`reports/` is **local-only working memory**: the whole directory is gitignored
(the repo is public; this layer is the development workflow, not the product).
It lives only on this machine — nothing in it is recoverable from git, so
deletion is real deletion. It is pruned on purpose, and the rules below are
what keep `reports/agents/` readable. Agents do NOT commit report files; the
"commit what you wrote" rule above applies to code and tracked docs only.

- **Naming.** An agent report's filename says what it is about:
  `YYYY-MM-DD_<topic-in-kebab-case>.md`. Timestamp-only names
  (`2026-08-14_subagent_09-42-33Z.md`) are not acceptable — a name nobody can
  triage from is a file nobody will ever reopen.
- **Deletion.** An agent report is deleted once **both** hold: (a) it has been
  folded into a synthesis under `reports/synthesis/`, and (b) no synthesis, ADR,
  plan, or backlog item links to it. Reports that are still linked stay, because
  deleting them breaks the traceability chain that is the whole point of this
  layer. Deletion is permanent — `reports/` is untracked, so there is no git
  history to recover from; when in doubt, keep the file.
- **Who prunes.** The pruning pass runs as its own unit of work with its own
  commit — never as a side effect of a synthesis run. The `report-synthesis`
  skill must not delete or edit agent reports.
- **Where the machinery lives.** The four hooks driving this layer are versioned in
  [`.claude/hooks/`](.claude/hooks/README.md) and wired through the committed
  `.claude/settings.json` — not in `~/.claude/`. Change them there, run
  `node .claude/hooks/tests/reports-hooks.test.mjs`, and commit. The skills this
  contract depends on are versioned alongside them in
  [`.claude/skills/`](.claude/skills/README.md), with the `code-reviewer-berke`
  subagent in `.claude/agents/`. A fresh clone can execute the contract.
- **`reports/agents/_auto/`.** The `SubagentStop` hook drops reconstructed
  transcript tails here when an agent finished without writing a real report.
  These are raw material, not reports: they are invisible to synthesis and carry
  no retention guarantee. Promote one by rewriting it with the `agent-report`
  skill and moving it up a directory; otherwise it gets pruned unread.
