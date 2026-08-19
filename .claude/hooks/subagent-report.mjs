// SubagentStop — GUARANTEES that a subagent's substantive findings are captured.
//
// Rationale: make it a property of the system rather than trusting agent
// discipline. If the agent already wrote its report via the agent-report skill,
// this hook stays out of the way; if it didn't, a report is reconstructed from
// the last assistant message in the transcript.
//
// Reconstructed dumps are NOT reports — they are raw transcript tails. They go to
// reports/agents/_auto/, which filesByMtime() ignores (leading `_`), so they never
// inflate the unsynthesized-report count or block the freshness check. A trivial
// final message (a one-line fix confirmation) is dropped entirely: capturing it
// costs more attention than it returns. Promote anything worth keeping out of
// _auto/ by rewriting it with the agent-report skill.

import fs from "node:fs";
import path from "node:path";
import {
  readInput, safe, findReportsRoot, stamp, today, slug,
  ensureDir, readTranscript, lastAssistantText, filesByMtime,
} from "./lib.mjs";

const FRESH_MS = 90_000; // a report written in the last 90s counts as the agent's own
const MIN_BODY = 1200;   // below this, a final message is a status line, not a finding

safe(async () => {
  const input = await readInput();
  const root = findReportsRoot(input.cwd);
  if (!root) return; // stay silent in projects without the reports/ architecture

  const agentsDir = path.join(root, "agents");
  if (!ensureDir(agentsDir)) return;

  // 1) Did the agent write its own report?
  const recent = filesByMtime(agentsDir)[0];
  if (recent && Date.now() - recent.mtime < FRESH_MS) return;

  // 2) It didn't — reconstruct one from the transcript, if there is enough there
  //    to be worth a file.
  const messages = readTranscript(input.transcript_path);
  const body = lastAssistantText(messages);
  if (!body || body.length < MIN_BODY) return;

  const autoDir = path.join(agentsDir, "_auto");
  if (!ensureDir(autoDir)) return;

  const agentName = slug(input.agent_name || input.subagent_type || "subagent", 32);
  const firstUser = messages.find((m) => m.role === "user");
  const task = (firstUser?.text || "").split("\n")[0].slice(0, 120).replace(/"/g, "'");

  const file = path.join(autoDir, `${today()}_${agentName}_${stamp().slice(11)}.md`);
  const fm = [
    "---",
    `agent: ${agentName}`,
    `task: "${task || "(not recorded)"}"`,
    `date: ${new Date().toISOString()}`,
    `session: ${input.session_id || "unknown"}`,
    "status: complete",
    "confidence: medium",
    "source: hook(SubagentStop)   # agent did not write one; reconstructed from transcript",
    "---",
    "",
    "> Generated automatically by the `subagent-report` hook. The body below is the",
    "> agent's final message — for a properly structured report the agent should",
    "> have used the `agent-report` skill. This file lives in `agents/_auto/` and is",
    "> invisible to synthesis. Promote it (rewrite via `agent-report`, move up one",
    "> directory) or delete it.",
    "",
    body.slice(0, 20000),
    "",
  ].join("\n");

  fs.writeFileSync(file, fm, "utf8");
});
