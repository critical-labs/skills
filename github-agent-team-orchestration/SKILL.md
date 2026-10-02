---
name: github-agent-team-orchestration
description: Use when the user wants multiple autonomous agents working a GitHub issue backlog unattended — "spin up a team", overnight/24h runs, agents resolving issues into fork PRs and cross-reviewing each other, runs that must survive usage-limit cutoffs and resume on their own.
---

# GitHub Agent Team Orchestration

## Overview

Run a team of worker agents against a repo's open issues with no human in the loop: each worker claims a bot identity, resolves issues (spec → tests → code), opens PRs from the agent fork to upstream `main`, reviews teammates' PRs, and babysits its own. The orchestrator (this session) never writes code — it routes work, supervises, and keeps the run alive across usage-limit cutoffs.

**Core principle:** GitHub + the ledger file are the source of truth, never any agent's context. Every worker is disposable and respawnable; the run must survive the death of everything, including you.

## When to Use

- "Spin up a team of agents on the issue backlog", unattended multi-hour/overnight runs, team cross-review requirements.
- **Not for:** one spec implemented by one agent (use `agentic-sdlc`); parallel same-session subtasks without GitHub state (use `dispatching-parallel-agents`).

## Iron Rules

1. **Every GitHub write goes through the agent identity** (forge tools or bot PAT). The human's `gh` auth is read-only (list/view/diff/checks). Identity path broken at 3am? That is NOT permission to use human creds — stage the work (git bundle + pre-written PR body files), retry the identity path on a 10-min backoff, leave a blocked-note. A credential file that returns "permission denied" is a boundary, not a bug to debug.
2. **Never merge during the run.** End state per issue: PR open, CI green, peer-reviewed. The human merges.
3. **One issue = one branch = one PR.** Nothing half-pushed at handoff: unfinished work goes up as a WIP fork branch + status comment on the issue.
4. **Checkpoint before anything can kill you.** The ledger write comes before risky calls, before bridging a usage gap, before ending any turn.
5. **Workers run heavy:** spawn with the strongest general model available (`model: "opus"` or better) and include the literal keyword `ultracode` in the kickoff prompt — the user's invocation of this skill is the explicit multi-agent opt-in that ultracode requires. Default-model, non-ultracode workers are a baseline failure, not a judgment call.

## Phase 0 — Preflight (orchestrator, once)

1. Repo sanity: clone exists, `git fetch origin main`, read branch ruleset (required reviews/checks; tolerate 403).
2. **Smoke-test the identity path end-to-end yourself** before spawning anyone: `ensure_identity{require:["github"]}` → confirm fork exists. Record whether identities map to ONE shared bot account (usual) or distinct accounts — this decides the review protocol (§Cross-review).
3. Snapshot `gh issue list` AND `gh pr list` — skip issues already claimed or linked to open PRs (other sessions/teams may be running; a claim comment by another agent is a claim).
4. Rank the queue (small, self-contained, unblocked first; defer `needs-design`/`discussion`/assigned). Write the ledger: `<scratchpad>/run/state.json` with queue, workers, review ring, timestamps, deadline, wind-down time.

## Identity & fork mechanics (hard-won — read before first delivery)

- Pool identities are claimed per-process (`~/.config/agent-identity/claims/*.lock` holds a pid). **Never steal a live claim** — check `ps -p`. Pool exhausted → onboard a free identity (backend `github` tag + link) or use the bot-PAT manual fallback; claims free up as other sessions end, so backoff-retry is usually enough.
- All pool identities typically share **one bot fork account** → every worker uses a distinct branch namespace: `agent/<worker>-issue-<N>`.
- Delivery: **pin `base` to a SHA and re-check `git diff --name-status <sha>..HEAD` immediately before delivering** — a stale branch diffed against a newer main delivers a REVERT of teammates' merged work.
- `422 GitRPC::BadObjectState` = fork `main` is stale → `gh api -X POST repos/<fork>/merge-upstream -f branch=main` (bot PAT), then PATCH the branch ref.
- Force-resetting a PR branch to exactly base auto-CLOSES the PR (0 commits ahead) — commit first or `gh pr reopen` after.
- Forge tools can return failures as `{"error":…}` body with no error flag — check the text, not just the status.
- Each worker session has its OWN attribution (session URL, identity email) — never bake the orchestrator's session trailer into worker prompts; tell workers to use their own.

## Spawning workers

Spawn N (default 3) background `Agent` calls in one block, `model` = strongest available, prompt from [kickoff-prompt.md](kickoff-prompt.md) with substitutions (worker id, branch prefix, scratch dir, repo, report format). Each worker gets its own clone/worktree — never share a working tree. Record agentIds in the ledger. Workers are message-driven: one work order (`ISSUE <N>` / `REVIEW PR <M>` / `BABYSIT`) per `SendMessage`, structured report back, stop. The orchestrator arbitrates issue assignment from the ledger queue — workers never self-assign (prevents races).

## Cross-review protocol

Fixed ring (w1→w2→w3→w1). When a worker reports a PR, immediately dispatch `REVIEW PR <M>` to its ring peer — **review orders outrank new issues** (PR latency starves the whole team; a blocked teammate is 100% wasted capacity).

- Distinct bot accounts → formal `gh pr review --approve|--request-changes` as the bot.
- **Shared single bot account → GitHub forbids self-review.** Reviewer posts a structured verdict comment instead: `**Peer review (w2): APPROVE|REQUEST_CHANGES**` + findings. The human remains the formal approval gate; say so in the handoff. Do not try to fake an approval.
- Reviews are real: diff + checkout + run the suite. No rubber stamps — an approval stakes the reviewer's correctness.

## Worker self-PR monitoring (goes in every kickoff)

Check streams at natural breakpoints (after each test run), never >30 min apart. Priority ladder:

1. **Maintainer/owner comment** — interrupt current work: checkpoint WIP (`git commit` with a note-to-self message), informative ack within ~15 min, substantive verified answer within the hour. Correctness flagged? **Draft the PR immediately**, before you know the answer — never let a maintainer-questioned PR sit mergeable. Re-verify before defending; the maintainer is usually right.
2. **Teammate blocked on your review** — next breakpoint, ≤1h blocked-on-you.
3. **Red CI on your PRs** — batch at breakpoints, fix within ~2h.
4. **Bot/non-blocking comments** — batch.
5. **New backlog claims** — only when your open PRs are green and unblocked. Merged PRs score; started issues don't.

## Supervision loop (orchestrator)

`ScheduleWakeup` tick every ~30 min; **each tick schedules its successor first-failure-proof** (plus a hard wake at wind-down and deadline). Per tick:

1. `ListAgents` + process worker reports → update ledger, dispatch next orders (REVIEW > BABYSIT > ISSUE).
2. Reconcile against GitHub read-only (`gh pr list --json number,state,reviewDecision,statusCheckRollup`). Ledger disagrees → GitHub wins.
3. Dead worker (gone from ListAgents, SendMessage errors, silent 2 ticks) → respawn fresh with kickoff + handoff paragraph built from reconciled state ("you replace w2; PR #510 open CI red; branch exists; reconcile, don't restart").
4. Stuck worker (same issue >3 ticks) → timebox order; one more tick → park it (WIP branch + issue comment, issue → deferred).
5. Heartbeat line into the ledger, reschedule.

## Usage-limit protocol

Detection: simultaneous worker deaths / limit-error text with a reset time.

1. **Checkpoint FIRST** (the one call that must not fail): full resume state into the ledger + one index line in memory `MEMORY.md` so the human finds it even if everything else fails. Include "uncommitted work in worktrees — do not prune".
2. **Compute the gap against real system time:** `date +%s` and `date '+%Z %z'` — the reset time in the error is wall-clock in some timezone; verify which before arithmetic, don't assume. Gap = reset_epoch − now_epoch + 300s buffer.
3. **Bridge:** ScheduleWakeup caps at 3600s/hop → chain ⌈gap/3600⌉ hops (intermediate wakes do nothing but reschedule — no exploration). Never tight-loop retries: three agents dying with identical reset messages is an account quota, not a flake, and retries burn the exact budget the checkpoint plan needs.
4. **Post-reset wake:** one cheap probe call; still limited → one 900s retry, then extend the bridge. Success → reconcile from GitHub (not the ledger), respawn ALL workers with handoff paragraphs, resume cadence.

## Wind-down & handoff

T−2h: stop assigning issues; REVIEW/BABYSIT only; park unshippable WIP. Deadline: final reconcile, then the human-facing report: PR table (number, issue, CI, review verdict, ready-to-merge?), deferred issues with reasons, parked WIP branches, **explicit "nothing was merged"**, decisions needing a human. Stop the wake chain.

## Common Mistakes

| Mistake | Reality |
|---|---|
| Per-worker forks assumed | Pool identities usually share one bot fork — distinct branch namespaces, comment-based peer review |
| Workers on default model, no ultracode | User asked for a heavy team; spawn opus-class + ultracode keyword |
| Diff/deliver against moving main | Pin base SHA; re-check the diff right before delivery or you ship a revert of peers' work |
| Redundant guess-wakes (+1h/+3h/+6h) after cutoff | Compute reset−now from `date` with TZ verified; bridge in 3600s hops + 5-min buffer |
| Orchestrator's session trailer in worker commits | Each worker uses its own session/identity attribution |
| "Approve so the ruleset is satisfied" with shared account | Self-review is impossible and faking it is worse; human is the gate |
| New issue while own PR has maintainer question | Draft the PR, answer first — an unanswered correctness flag is the only unrecoverable move |

## Red Flags — STOP

- "Just this once with the user's gh" / "attribution note in the PR body makes it OK"
- "Merge it so the team isn't blocked"
- "Retry spawning until the limit clears"
- "I'll remember the state" — the ledger write comes first, always
- "The fork's main is probably fine" before a delivery
