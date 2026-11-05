---
name: github-agent-team-orchestration
description: Use when the user wants multiple autonomous agents working a GitHub issue backlog unattended — "spin up a team", overnight/24h runs, agents resolving issues into fork PRs and cross-reviewing each other, runs that must survive usage-limit cutoffs and resume on their own.
---

# GitHub Agent Team Orchestration

## Overview

Run a team of worker agents against a repo's open issues with no human in the loop: each worker claims a bot identity, resolves issues (spec → tests → code), delivers a fork branch plus a handoff brief, reviews teammates' PRs, and babysits its own. A separate **technical writer** worker (`tw`) turns each delivered branch into the PR title and description the maintainer reads, then opens the PR. The orchestrator (this session) never writes code or prose — it routes work, supervises, and keeps the run alive across usage-limit cutoffs.

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
6. **Implementers never write their own PR description.** The worker that wrote the code writes a handoff brief (facts); the technical writer, with only the issue, the brief and the diff in context, writes the title and body for the human and opens the PR. A body written from inside the implementation context reads as the author's notes to itself. See §PR descriptions.
7. **Open PRs are capped by CI capacity, not by how fast the team can write code.** A delivery or push past the cap is staged, not shipped. A team that out-produces the runners doesn't speed anything up: it buries every PR (and the maintainer's deploys and monitors) under a CI queue. See §CI budget.

## Phase 0 — Preflight (orchestrator, once)

1. Repo sanity: clone exists, `git fetch origin main`, read branch ruleset (required reviews/checks; tolerate 403).
2. **Smoke-test the identity path end-to-end yourself** before spawning anyone: `ensure_identity{require:["github"]}` → confirm fork exists. Record whether identities map to ONE shared bot account (usual) or distinct accounts — this decides the review protocol (§Cross-review).
3. Snapshot `gh issue list` AND `gh pr list` — skip issues already claimed or linked to open PRs (other sessions/teams may be running; a claim comment by another agent is a claim).
4. Rank the queue (small, self-contained, unblocked first; defer `needs-design`/`discussion`/assigned). Write the ledger: `<scratchpad>/run/state.json` with queue, workers, review ring, timestamps, deadline, wind-down time.
5. Production uptime preflight: health URLs + an authorized recovery path (§Production uptime loop), then start the uptime cron.
6. CI budget preflight (§CI budget): count the runners that serve PR checks, measure a typical PR's CI time, check whether the PR workflows skip drafts, count the open bot PRs that still need CI (non-draft, not green), and write `ci_budget` into the ledger. If open bot PRs are already at or over the cap, the run starts in **stage mode**: no new PRs until the count drops.
7. Description preflight (§PR descriptions): record `edit_path` (is `forge_update_pr` in the tool list?), check that the repo has the `pr-description-labels` workflow and the two labels, and sweep open bot PRs whose body lacks `<!-- tw:written -->` into `rewrite_queue` (read-only: `gh pr list --author <bot> --json number,body,labels`).

## Identity & fork mechanics (hard-won — read before first delivery)

- Pool identities are claimed per-process (`~/.config/agent-identity/claims/*.lock` holds a pid). **Never steal a live claim** — check `ps -p`. Pool exhausted → onboard a free identity (backend `github` tag + link) or use the bot-PAT manual fallback; claims free up as other sessions end, so backoff-retry is usually enough.
- All pool identities typically share **one bot fork account** → every worker uses a distinct branch namespace: `agent/<worker>-issue-<N>`.
- Delivery: **pin `base` to a SHA and re-check `git diff --name-status <sha>..HEAD` immediately before delivering** — a stale branch diffed against a newer main delivers a REVERT of teammates' merged work.
- `forge_deliver` builds the new branch on the **fork's `main`**, not on the `base` you pass. A stale fork main delivers a branch with the wrong parent. Check before delivering: compare the fork main's SHA with upstream main's (`gh api repos/<fork>/commits/main -q .sha`, read-only). If it's stale, sync it through the identity path (forge sync tooling or the bot PAT) — never with the human's gh, even though the human can push to the fork.
- `forge_deliver` squashes each call into ONE commit. With per-commit checks (commitlint scope, one component per commit) a whole-branch delivery can never pass: deliver one call per local commit. It only appends to an existing branch and can't rewrite it, so a bad branch means a new branch + PR (the maintainer closes the old one; the bot can't). It can't set file modes either (exec bits are lost).
- `422 GitRPC::BadObjectState` = fork `main` is stale → `gh api -X POST repos/<fork>/merge-upstream -f branch=main` (bot PAT), then PATCH the branch ref.
- Force-resetting a PR branch to exactly base auto-CLOSES the PR (0 commits ahead) — commit first or `gh pr reopen` after.
- Forge tools can return failures as `{"error":…}` body with no error flag — check the text, not just the status.
- Each worker session has its OWN attribution (session URL, identity email) — never bake the orchestrator's session trailer into worker prompts; tell workers to use their own.

## Spawning workers

Spawn N (default 3) implementer workers plus one technical writer as background `Agent` calls in one block, `model` = strongest available. Implementers use [kickoff-prompt.md](kickoff-prompt.md), the writer uses [technical-writer-prompt.md](technical-writer-prompt.md), with substitutions (worker id, branch prefix, scratch dir, shared `HANDOFF_DIR`, repo, fork). Each worker gets its own clone/worktree — never share a working tree. Record agentIds in the ledger (`workers.w1…`, `workers.tw`). Workers are message-driven: one work order (`ISSUE <N> ship: deliver|stage` / `DELIVER <staged-path>` / `REVIEW PR <M>` / `BABYSIT` / `OPEN PR` for implementers; `WRITE PR <N>` / `UPDATE PR <M>` for the writer) per `SendMessage`, structured report back, stop. The orchestrator arbitrates issue assignment from the ledger queue — workers never self-assign (prevents races).

## CI budget and the open-PR cap

Every open PR costs CI: a full run on open, and another on every push. When the team's demand outruns the runners, the queue grows without bound. All PRs then wait hours for a verdict, and fixups for red checks wait behind new work. On a shared self-hosted runner, the queue also starves the maintainer's deploys and scheduled jobs such as the prod uptime monitor.

What that looked like (homefree, 2026-11-04/05): 2 implementers on 2 runners produced 7× the CI capacity. 133 PRs were open, 158 runs were queued, and the oldest had waited 15.7 h. The scheduled prod uptime monitor stopped running (its watchdog filed an issue), and only 29 PRs ever got a verdict. The fix was a manual freeze, "no new PRs except security fixes; stage the rest", which the cap below makes the default.

**The budget (ledger `ci_budget`, set at preflight, adjustable by the maintainer):**

- `max_open_prs`: bot PRs that still need CI, meaning open, not draft, and without a green required gate. Default **2 × implementers**, never above **runners × 3**.
  - **Excluded:** a green PR waiting only for the maintainer's merge costs no CI until it's pushed again. Report those separately (`awaiting merge: N`), so a slow merge rate stays visible without blocking delivery.
  - **Drafts:** they are excluded **only if the repo's PR workflows skip drafts.** Check the triggers at preflight: `types:` should include `ready_for_review`, and each job should have `if: !github.event.pull_request.draft`. If the workflows run on drafts, a draft costs the same as a ready PR and counts. Record which applies as `ci_budget.drafts_skip_ci`.
- `max_queue_age`: when the oldest queued PR run is older than this (default 60 min), stop new deliveries even under the cap.
- `security_reserve`: 1 slot above the cap, only for a fix to a live, exploitable issue. A security fix past the reserve still goes in, but tell the user in the same turn.

**Measuring it, read-only, at each dispatch and each heartbeat:**

- PRs that still need CI: `gh pr list -R <repo> --author <bot> --state open --json number,isDraft,statusCheckRollup --jq '[.[] | select(.isDraft|not) | select(([.statusCheckRollup[]? | select(.name=="<gate>") | .conclusion] | first) != "SUCCESS")] | length'` (`<gate>` is the repo's required gate check; count drafts too when `drafts_skip_ci` is false)
- queue depth and age: `gh run list -R <repo> --status queued --json createdAt --limit 200`

**Dispatch rules:**

1. **Under the cap and the queue is fresh** → `ISSUE <N> ship: deliver`.
2. **At the cap, or the queue is too old** → `ISSUE <N> ship: stage`. The worker builds and tests as normal, then writes a self-sufficient bundle plus brief (kickoff-prompt.md). The orchestrator adds it to `delivery_queue` in the ledger, with priority (security > prod-safety > fixes > features > docs) and dependencies.
3. **A slot opens** (a PR merged or closed, or the queue drained) → send `DELIVER <staged-path>` for the highest-priority staged item whose dependencies have merged, before any new `ISSUE ship: deliver`. The worker rebases and re-tests first; a staged branch is never delivered as is.
4. **Pushes cost runs too.** Push to an open PR only on a red check or a reviewer's requested change, one batched push per review round. A reviewer nit that needs no fix to merge goes in the verdict, not in a push. Tell the reviewer that too.
5. **Supersede sparingly.** A `-v2` PR adds an open PR until the maintainer closes the old one. Supersede only when the old branch truly can't be fixed in place (`forge_deliver` can't rewrite history). Put the old number on the close list the same turn, and count both until it's closed.
6. **Don't re-run cancelled runs while over budget.** A re-run adds to the same queue the cap protects. Re-queue cancelled runs only when `max_queue_age` is met and no deploy is in progress.
7. **Already far over the cap** (for example, the run starts with dozens of PRs open)? Staging stops it getting worse. Getting back under means draining, which is the maintainer's call, because the bot's tools can't set draft state or close PRs:
   - Write a plan, `pr-draft-plan.md`, with three lists:
     - **keep ready:** PRs green and awaiting merge, live security fixes, and the prerequisites of either;
     - **close:** superseded PRs;
     - **draft:** everything else, in un-draft priority order.
   - Include the ready-to-run `gh pr close` / `gh pr ready --undo` loops for the maintainer.
   - If drafts still run CI, also draft the workflow patch that skips them. It's `.github/`, so it's the maintainer's to apply.
   - Cancelling runs already queued for drafted PRs is a separate yes from the maintainer.
   - Afterwards, un-drafting (`gh pr ready`, by the maintainer, or by the bot if the identity proxy gains a draft tool) works like a `DELIVER`: the top draft goes ready when a slot opens. Rebase it first if main has moved.
8. **Cap reached but workers idle?** That isn't a reason to raise the cap. Staged work, reviews, audits, design proposals and the delivery queue's rebases are all real work. Raise the cap only on the maintainer's word, and record the new value in the ledger.

**Report the budget** in every heartbeat line (`needs-CI 9/4 · awaiting merge 23 · drafts 71 · queue 158 oldest 15.7h · staged 12`) and in the handoff report, with the delivery queue in priority order, so the maintainer sees the backlog the cap is holding back.

## PR descriptions (technical writer)

The maintainer's first contact with every PR is its description, and it is the one artefact the run produces for a human rather than for CI. Descriptions written by the implementer come out terse and jargon-heavy — fragments, an identifier in every sentence, bare issue numbers, no "why" — because the author's context is full of the implementation. The fix is structural, not a style note: a different worker with a clean context writes it.

**Flow per issue:**

1. Implementer delivers the branch and writes the handoff brief at `<HANDOFF_DIR>/issue-<N>.md` (template in kickoff-prompt.md: intent, what a person sees today and after, approach, the decision to question, assumptions with cost, what the tests prove in words, what was not verified, risks, related PRs, read-first file, the reviewer question, commit split, raw test output). It reports `pr: none | branch: … | handoff: …`.
2. Orchestrator records the PR as `awaiting-description` in the ledger and sends the writer `WRITE PR <N> by <worker>`. **`WRITE PR` outranks `REVIEW`** in dispatch order: until the PR exists, neither the peer review nor the CI watch can start.
3. Writer reads the issue, the brief and the diff (never the implementer's transcript), writes to the shape in technical-writer-prompt.md (Why / What changes / How it works / How to check it / Risks and choices / For the reviewer / footer / collapsed evidence appendix; 300–700 words before the appendix), runs the lint script, then the **cold read** (a fresh agent playing the maintainer, given only the body, must answer what, for whom, what changes, how to verify, what is risky, where to start, and list every term it cannot explain), fixes what the reader could not answer, re-checks the body against the current branch head, and opens the PR with `forge_open_pr` as its own identity. Reports `pr: #M | cold_read: …`.
4. Orchestrator updates the ledger, sends the implementer `BABYSIT` (CI watch starts there) and the ring peer `REVIEW PR <M>`.
5. A behaviour-changing push after review → implementer appends a dated note to the brief and reports `handoff: updated` → orchestrator sends the writer `UPDATE PR <M>` (bot-PAT `gh pr edit`, or a `**Description update (tw)**` comment when no edit path exists).

**Fallbacks (the branch never waits on the writer):**

- Writer reports `blocked: identity-pool` → it still writes; orchestrator sends the implementer `OPEN PR <N> <title-file> <body-file>` to post the files verbatim.
- Writer dead or silent 2 ticks → respawn fresh (it holds no state; everything is in the brief and the diff). At the deadline with a branch still `awaiting-description`, the implementer opens a **draft** PR whose body is the brief under a `Description pending — technical writer` heading, so nothing is half-pushed.
- Writer reports `context: stale` or `orders_done: 6` → respawn fresh before the next order. A writer that has read many diffs drifts back toward implementer prose. Rewrites count as orders.

**Labels and the marker (GitHub as the source of truth):** every writer-produced body ends with the hidden line `<!-- tw:written -->`. The repo-side workflow [pr-description-labels.yml](pr-description-labels.yml), installed by the maintainer, labels every bot PR `description:written` when the marker is present and `needs-description` when it is not, on open, edit and push. Reconciliation is then a label query, not a ledger read: `needs-description` = rewrite owed, no PR for a delivered fork branch = write owed. The agents never touch labels themselves; the forge tools cannot, and the human's `gh` is read-only.

**Old PRs (`REWRITE PR <M>`):** PRs opened before this flow, or by an implementer during a writer outage, carry implementer prose. The preflight sweep queues them; the writer rewrites from the old body (as the brief), the linked issue and the diff, keeps the old body verbatim in a collapsed "Original description" block, and posts through the PR edit path. The writer's own queue runs `WRITE PR > UPDATE PR > REWRITE PR`; rewrites fill the writer's idle time and never delay a new PR.

**The PR edit path is a prerequisite, not an assumption.** `UPDATE PR` and `REWRITE PR` need the identity proxy's `forge_update_pr` tool (agent-identity ≥ 0.5: edits the title/body of a PR the bot account opened, carries the attribution footers forward, refuses any other author). Older proxies offer only open-PR and comment. Check the writer's tool list at preflight and record `edit_path`; without the tool, the writer delivers rewritten descriptions as a `**Description (tw)**` comment, the label stays `needs-description`, and the final report lists those PRs for the human to paste in. Labels and draft state are never the proxy's job. Do not route around a missing tool with a bot PAT or the human's credentials.

**What the orchestrator checks on each `WRITE PR` report:** `cold_read` is `pass` or `pass-after-rewrite` (a `shipped-with-leftovers` list goes into the handoff report for the human), and `words` is within 300–700. Two consecutive out-of-shape reports → respawn the writer.

## Cross-review protocol

Fixed ring (w1→w2→w3→w1); the writer is outside the ring. When the writer reports a PR, immediately dispatch `REVIEW PR <M>` to the implementer's ring peer — **review orders outrank new issues** (PR latency starves the whole team; a blocked teammate is 100% wasted capacity).

- Distinct bot accounts → formal `gh pr review --approve|--request-changes` as the bot.
- **Shared single bot account → GitHub forbids self-review.** Reviewer posts a structured verdict comment instead: `**Peer review (w2): APPROVE|REQUEST_CHANGES**` + findings. The human remains the formal approval gate; say so in the handoff. Do not try to fake an approval.
- Reviews are real: diff + checkout + run the suite. No rubber stamps — an approval stakes the reviewer's correctness.
- The reviewer also checks the description against the diff: a claim the diff does not support is a `REQUEST_CHANGES` finding, routed to the writer as `UPDATE PR`, not to the implementer.

## Worker self-PR monitoring (goes in every kickoff)

Check streams at natural breakpoints (after each test run), never >30 min apart. Priority ladder:

1. **Maintainer/owner comment** — interrupt current work: checkpoint WIP (`git commit` with a note-to-self message), informative ack within ~15 min, substantive verified answer within the hour. Correctness flagged? **Draft the PR immediately**, before you know the answer — never let a maintainer-questioned PR sit mergeable. Re-verify before defending; the maintainer is usually right.
2. **Teammate blocked on your review** — next breakpoint, ≤1h blocked-on-you.
3. **Red CI on your PRs** — batch at breakpoints, fix within ~2h.
4. **Bot/non-blocking comments** — batch.
5. **New backlog claims** — only when your open PRs are green and unblocked. Merged PRs score; started issues don't.

## Supervision loop (orchestrator)

Two wake sources keep the run alive; use both:

- **Event-driven (primary):** a worker's report (task notification / hand-back) re-invokes the orchestrator, which dispatches the next order at once. This chain runs itself, but it stops silently if a worker hangs or dies without reporting. An open session is not enough on its own, because nothing re-invokes an idle orchestrator.
- **Heartbeat (safety net):** a tick every ~30 min that does nothing when all is well. Set it up right after spawning the workers:
  - **In `/loop` dynamic mode:** `ScheduleWakeup`; **each tick schedules its successor first-failure-proof** (plus a hard wake at wind-down and deadline).
  - **Otherwise:** `CronCreate` a recurring job off the :00/:30 marks (e.g. `13,43 * * * *`) with the heartbeat prompt below. It is session-only (it dies with the session) and auto-expires after 7 days. Tell the user both, and record the job id in the ledger (`supervision.heartbeat_cron`); `CronDelete` it at the final handoff.

Heartbeat prompt (substitute repo, ledger path, worker agent ids):

```
[Scheduled agent-team heartbeat — not a user message] Supervise the <repo> agent-team run per the
github-agent-team-orchestration skill, using the ledger <ledger>. Keep it quiet and cheap: if everything
is progressing, append a one-line heartbeat to the ledger and say nothing else.
1. ListAgents. If a worker has an order in the ledger but has been silent for 2+ heartbeats, SendMessage
   it a status nudge; if SendMessage errors, respawn it fresh (opus, ultracode) with a handoff paragraph
   built from GitHub state.
2. Reconcile open agent PRs read-only (gh pr list --json number,headRefName,reviewDecision,statusCheckRollup)
   and measure the CI budget (open bot PRs vs ci_budget.max_open_prs; oldest queued run vs max_queue_age).
   Re-run CI runs that were CANCELLED (not failed) on open agent PRs only when within budget and no deploy
   run is in progress (a single shared runner is reserved for deploys then).
3. If a branch is awaiting-description and the writer is idle, dispatch WRITE PR; if a worker is idle and
   a REVIEW is owed, dispatch it; if a PR slot is free and delivery_queue is non-empty, dispatch DELIVER of
   its top item; otherwise dispatch the next ISSUE with ship: deliver when under budget and ship: stage
   when not (WRITE PR > REVIEW > BABYSIT > DELIVER > ISSUE). Never merge anything except PRs the maintainer
   already approved with merge-when-green.
4. Only surface something to the user if it needs them: a decision, a blocked worker, a failed (not
   cancelled) check, or the queue finishing.
```

Per tick:

1. `ListAgents` + process worker reports → update ledger, dispatch next orders (WRITE PR > REVIEW > BABYSIT > DELIVER > ISSUE). Every `ISSUE` order carries `ship: deliver|stage` from the current CI budget (§CI budget).
2. Reconcile against GitHub read-only (`gh pr list --json number,state,reviewDecision,statusCheckRollup,labels`). Ledger disagrees → GitHub wins. `needs-description` on an open bot PR → it belongs in `rewrite_queue`; a fork branch `agent/*-issue-*` with no PR → it belongs in `awaiting-description`.
3. Dead worker (gone from ListAgents, SendMessage errors, silent 2 ticks) → respawn fresh with kickoff + handoff paragraph built from reconciled state ("you replace w2; PR #510 open CI red; branch exists; reconcile, don't restart").
4. Stuck worker (same issue >3 ticks) → timebox order; one more tick → park it (WIP branch + issue comment, issue → deferred).
5. **Cancelled ≠ failed.** On a shared self-hosted runner, the maintainer may cancel queued PR runs on purpose to let a deploy through. Re-queue cancelled runs only once no deploy is in progress, and don't report them as red CI. A worker's job is never "rerun CI" (the bot usually can't); the orchestrator does it.
6. Heartbeat line into the ledger with the budget (`needs-CI 9/4 · awaiting merge 23 · drafts 71 · queue 158 oldest 15.7h · staged 12`), reschedule (ScheduleWakeup mode only; a cron re-fires on its own).

## Production uptime loop (orchestrator)

An agent team merges and deploys a lot, so production can go down mid-run while every PR looks green. If the repo has a production health URL, watch it for the whole run and be ready to recover it yourself.

**Preflight (Phase 0, once):**

1. **Find the health URLs.** Look in project memory first, then README/deploy docs, the deploy scripts' post-deploy checks, the Caddyfile/ingress hosts, and the prod env manifest. Watch every public host (the app, `/api/health`, any secondary domain such as a mobile/gateway host) plus the repo's own health endpoint. Record them in the ledger as `uptime.urls` with the expected status. If no URL is known, record `uptime: none found`, say so in the kickoff report, and skip this section. Never guess production hosts.
2. **Find the recovery path, and prove that it's authorized.** Look for the repo's rollback entry point: a rollback script or workflow (`rollback-prod.sh`, a `workflow_dispatch` rollback, re-deploying the last good release tag) and how it is invoked from here. Record the exact command, how it learns the last good version, and how to verify afterwards in `uptime.recovery`. Then establish that **you may run it unattended**:
   - A standing user instruction counts, for example a memory saying "if prod is down, roll back immediately".
   - So does a permission rule that allows the exact command.
   - Never assume it. A rollback blocked at 3am by a permission prompt or classifier is the same as having no rollback.
   - If it isn't authorized, ask the user **now, during preflight**, and record the answer as `uptime.recovery.authorized: true|false`.
   - With `false`, the loop still runs, but it ends in an alert instead of an action.
3. **Prefer recovery that lives in the repo over recovery that lives in this session.** Examples are a post-deploy health gate with auto-rollback, or a scheduled uptime workflow that rolls back on its own. They keep working when this session is dead, rate-limited or blocked. If the repo has neither, file an issue for it and give it high priority in the queue.

**Loop:** a separate `CronCreate` job, more often than the heartbeat. Use every 5 min (`*/5 * * * *`), tightened to every minute for 15 min after any prod deploy. Record its id as `supervision.uptime_cron` and delete it at handoff. It is cheap and silent when healthy. The prompt:

```
[Scheduled uptime check — not a user message] Check <repo> production per the github-agent-team-orchestration
skill, ledger <ledger> (uptime.*). Silent if healthy: one ledger line, no output.
1. curl each uptime.urls entry (-sS -o /dev/null -w '%{http_code} %{time_total}' --max-time 10). Healthy = expected
   status from every URL.
2. Not healthy → confirm before acting: retry twice, 20s apart. Also check DNS/TLS from here, and whether a prod
   deploy is in progress (gh run list for the deploy/promote workflows). A deploy in progress that already has its
   own health gate → wait for it to finish, then re-check.
3. Confirmed down → incident (below).
```

**Incident (confirmed down):**

1. **Checkpoint first.** Write `uptime.incident` to the ledger: the start time, the failing URLs with status and body excerpt, and the current and last-good versions.
2. **Alert the user at once** with `PushNotification` if available, otherwise in chat. Keep it short: what is down, since when, and what you are about to do. Do this even when you can recover on your own.
3. **If `authorized: true`, recover first and diagnose afterwards.**
   - Run `uptime.recovery` to roll back to the last known good release. Don't fix forward in production.
   - Don't edit the box's env or config by hand unless that exact step is the recorded recovery.
   - Then verify with the same URL checks, plus 3 consecutive healthy checks over at least 2 minutes.
   - Still down after one rollback → don't loop rollbacks. Escalate to the user with everything collected.
4. **If not authorized, or the action is blocked, don't work around the block.**
   - Hand the user the exact ready-to-run command (`! <cmd>`) and the evidence.
   - Re-alert every 15 min until it's acknowledged or prod recovers.
   - Keep checking at 1-min cadence so you report recovery the moment it happens.
5. **Freeze the cause.**
   - Pause dispatching new ISSUE orders that touch deploy or infra paths, and stop re-queuing CI that could deploy.
   - Find the release that caused it: the last deploy before the incident started.
   - File an issue as the bot with the timeline, root cause once known, and the fix PR. Put it at the head of the queue, ahead of all ISSUE work.
6. **Close.** Write `uptime.incident.resolved` to the ledger and send the user one message covering downtime, the action taken, and the root cause. Add a memory line if you learned something new about this production, such as a new URL or a recovery gotcha.

## Usage-limit protocol

Detection: simultaneous worker deaths / limit-error text with a reset time.

1. **Checkpoint FIRST** (the one call that must not fail): full resume state into the ledger + one index line in memory `MEMORY.md` so the human finds it even if everything else fails. Include "uncommitted work in worktrees — do not prune".
2. **Compute the gap against real system time:** `date +%s` and `date '+%Z %z'` — the reset time in the error is wall-clock in some timezone; verify which before arithmetic, don't assume. Gap = reset_epoch − now_epoch + 300s buffer.
3. **Bridge:** ScheduleWakeup caps at 3600s/hop → chain ⌈gap/3600⌉ hops (intermediate wakes do nothing but reschedule — no exploration). Never tight-loop retries: three agents dying with identical reset messages is an account quota, not a flake, and retries burn the exact budget the checkpoint plan needs.
4. **Post-reset wake:** one cheap probe call; still limited → one 900s retry, then extend the bridge. Success → reconcile from GitHub (not the ledger), respawn ALL workers with handoff paragraphs, resume cadence.

## Wind-down & handoff

T−2h: stop assigning issues; WRITE PR/REVIEW/BABYSIT only; park unshippable WIP; every delivered branch gets its PR before the deadline (draft with the brief as body if the writer cannot). Deadline: final reconcile, then the human-facing report: PR table (number, issue, CI, review verdict, description written/rewritten/as-comment, ready-to-merge?), deferred issues with reasons, parked WIP branches, PRs still labelled `needs-description`, the CI budget and the staged delivery queue in priority order (with dependencies), **explicit "nothing was merged"**, decisions needing a human, any prod incidents. Stop the wake chain and `CronDelete` the heartbeat and uptime crons.

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
| Rollback authority discovered during the outage | Prove the recovery command runs unattended in preflight; a blocked rollback at 3am is no rollback |
| "Every PR is green" taken to mean prod is fine | Only the uptime loop knows; deploys from merged PRs can take prod down after CI passed |
| Implementer writes its own PR body ("I have all the context") | That context is the defect: the body comes out as notes-to-self. Brief → writer → cold read → PR |
| Writer skips the cold read ("it reads fine to me") | The writer has just read the diff; only a reader who hasn't can tell what is unexplained |
| Writer reused across the whole run | After ~6 diffs it writes like an implementer; respawn fresh |
| "Workers are idle, so open more PRs" | Past the CI cap a PR only lengthens the queue. Stage it; it ships when a slot opens |
| A `-v2` PR per failed check, a push per review nit | Each is a full CI run and the v2 holds a second slot. Fix in place, batch pushes, leave nits in the verdict |
| Re-running cancelled runs to "help" the queue | It adds to the queue. Only within budget, and never during a deploy |
| "The writer can `gh pr edit` with the bot PAT" | Workers hold no PAT; the edit path is a proxy tool or nothing. Comment fallback + report, never a credential hunt |

## Red Flags — STOP

- "Just this once with the user's gh" / "attribution note in the PR body makes it OK"
- "Merge it so the team isn't blocked"
- "Retry spawning until the limit clears"
- "I'll remember the state" — the ledger write comes first, always
- "The fork's main is probably fine" before a delivery
- "Just one more PR over the cap, it's small" (stage it; the cap counts PRs, not lines)
- "I'll open the PR myself, the description will take two minutes" (implementer) / "skip the cold read, it's clear" (writer)
- "Prod is down, the rollback is blocked — I'll edit the box another way" (alert the user with the command instead)
