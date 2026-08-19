# Agent Coordination

Protocol for avoiding commit and working-tree conflicts when multiple agents (main
session or sub-agents) operate on this repo in parallel, sharing one working directory.

## Active-agent registry

`.claude/active-agents.md` is a live scratch board — gitignored, local-only, never
committed. Every agent reads and updates it.

**Before starting work:**
1. Read `.claude/active-agents.md`. If it doesn't exist, create it using the format below.
2. Check whether any listed entry's scope (files/dirs) overlaps with what you're about to
   touch.
3. If it overlaps, don't touch those files — wait, or narrow your scope to the
   non-overlapping part, and note the conflict in your final message.
4. If clear, append a row for yourself: your agent id/session handle, scope (files/dirs),
   status (`in progress`), start time.

**Before committing** (in addition to running relevant tests/checks, per the Agent rules
in `CLAUDE.md`):
1. `git status --porcelain` — see everything that's changed in the working tree.
2. `git diff --name-only` / `git diff --cached --name-only` — confirm every file you're
   about to stage is one you actually wrote, and that nothing else has been swept in.
3. If files outside your scope show changes you didn't make, another agent has in-flight
   work — do not stage or commit them, and never use `git add -A`. Stage only your own
   files, individually, by name.
4. If your own files show modifications you don't recognize, stop and report it in your
   final message instead of committing over it.

**When finished:**
1. Commit your own files (per the Agent rules in `CLAUDE.md`).
2. Remove your row from `.claude/active-agents.md` (or mark it `done`).

## Registry format

```markdown
| agent | scope | status | started |
|---|---|---|---|
| report-writer-1 | report/weekly/2026-08-10.md | in progress | 2026-08-10T10:00 |
```
