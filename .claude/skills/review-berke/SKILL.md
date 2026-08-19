---
description: >
  Reviews changed code for correctness bugs and unnecessary complexity, then
  reports findings — it never edits code. With no argument it reviews the
  current uncommitted changes; with an argument it reviews the given file,
  directory, or branch. Use when the user types "/review-berke" or asks for a
  code review.
argument-hint: "[file | directory | branch | empty = current changes]"
allowed-tools: >
  Read Grep Glob
  Bash(git diff *) Bash(git status *) Bash(git log *) Bash(git show *)
  Bash(git rev-parse *) Bash(git branch *)
shell: bash
---

# Code review: ${ARGUMENTS:-current changes}

## Collected context

Repository state:
!`git rev-parse --is-inside-work-tree 2>/dev/null || echo "NO-GIT: this is not a git repository"`

Changed files:
!`git status --short 2>/dev/null || echo "(no git — take the scope from the argument)"`

Stat of uncommitted changes:
!`git diff HEAD --stat 2>/dev/null || echo "(none)"`

Diff:
!`git diff HEAD 2>/dev/null | head -c 60000 || echo "(diff unavailable)"`

## What to do

Using the context above, delegate the review to the `code-reviewer-berke`
subagent. The subagent cannot see this conversation, so state the following
**explicitly** in the task you send it:

1. **Scope.** Exactly what is under review:
   - No argument: the files changed in the diff above. List them one by one.
   - Argument is a file or directory path: that path.
   - Argument is a branch name: the `git diff <branch>...HEAD` range.
   - Not a git repository and no argument: ask the user which file or
     directory to review. Do not guess.
2. **The absolute path of the working directory**, so the subagent can locate
   the files.
3. **Focus:** correctness bugs first, simplification and quality second.
4. **Read-only:** it must not modify any file; report only.
5. If the diff is large, tell the subagent to read the files itself via
   `git diff` / `Read` rather than truncating an inlined diff.

If the scope is ambiguous — which branch to compare against, which directory —
**ask instead of guessing.** A review run on the wrong scope is both wasted and
misleading.

## Result

Relay the subagent's report to the user **verbatim**, preserving the severity
ordering and the `file:line` references. Do not reinterpret, soften, or filter
out findings as "not really a problem".

Then add one line: if they want the findings applied, they only need to say so
— the reviewer does not fix anything, you apply fixes separately.

Under no circumstances edit files as part of this command.
