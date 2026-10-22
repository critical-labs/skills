---
name: pause
description: Use when the user says "pause", "pause all", "park this session", "stop everything for now", or otherwise wants all in-progress work halted and the session left resumable — including mid-task, with workers, sub-agents, background shells, dev servers, or scheduled wakeups still running.
---

# Pause

## Overview

Park the session so that **a fresh session with zero context could resume it**. Pausing is not just stopping processes — it is stopping processes *and* making the work durable. The pause is complete only when the parked banner is printed.

**Core principle: a pause must survive the death of this session.** Anything that exists only in your context or the chat scrollback is lost.

## Checklist (in order)

1. **Stop starting.** No new work beyond the pause itself. Answer any in-flight question in the final report instead.
2. **Halt everything you started — and nothing you didn't.**
   - Background shells, dev servers, watch loops: kill by your recorded pids/task ids, then verify the ports are free.
   - A process you did not start (check owner, start time, command against your own records) is **not yours to kill** — note it in the report and leave it alone.
   - Sub-agents / workers: stop them (TaskStop / SendMessage "park now"); record in the resume notes what each was doing and whether it parked cleanly, so it can be re-dispatched.
   - Scheduled wakeups, self-pacing loops, crons: cancel them. Nothing may fire and silently resume after you have said "paused".
3. **Make the work durable.**
   - Dirty worktree → local **WIP commit** (message starts `wip:` and says "paused"). If commit hooks reject the honest WIP state, bypass them only where your environment's own rules allow; otherwise record the blocked, still-dirty worktree prominently in the resume notes. Never `git stash` on a shared stash stack, never push or open PRs just because you are pausing.
   - Files in `/tmp` or other swept locations → copy somewhere durable.
4. **Write resume notes a cold session can act on** — a persistent memory/notes file (indexed if your memory system has an index), not just chat. Must contain: what is already delivered (PRs, issues, SHAs); the exact resume point (file + next concrete action); branch and WIP-commit SHA; what each stopped worker was doing; open blockers and what they wait on; how to verify the state (test commands).
5. **Report and stamp.** A short final message — delivered / parked / waiting-on — ending with the parked banner. **Use the template below verbatim** (exactly these 10 lines, full width); replace only the `<STATUS LINE n>` slots with short status cues (≤14 chars each, pad with spaces). Do not design your own banner.

```
################################################################################################
##                                                                                            ##
##  ██████╗  █████╗ ██╗   ██╗███████╗███████╗██████╗      ▄█████▄  ▄█████▄                    ##
##  ██╔══██╗██╔══██╗██║   ██║██╔════╝██╔════╝██╔══██╗     ███████  ███████   <STATUS LINE 1>  ##
##  ██████╔╝███████║██║   ██║███████╗█████╗  ██║  ██║     ███████  ███████   <STATUS LINE 2>  ##
##  ██╔═══╝ ██╔══██║██║   ██║╚════██║██╔══╝  ██║  ██║     ███████  ███████   <STATUS LINE 3>  ##
##  ██║     ██║  ██║╚██████╔╝███████║███████╗██████╔╝     ███████  ███████   <STATUS LINE 4>  ##
##  ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝╚═════╝      ▀█████▀  ▀█████▀   <STATUS LINE 5>  ##
##                                                                                            ##
################################################################################################
```

## Resuming

On "resume": read the resume notes, verify the recorded state still holds (branches moved? processes gone?), then continue from the recorded resume point — do not re-derive the plan from scratch.

## Common rationalizations

| Excuse | Reality |
|--------|---------|
| "The user asked to pause, not to checkpoint/commit" | A pause with no WIP commit is a dangling worktree another session, a cleanup job, or a crash can destroy. Committing locally IS the pause. |
| "Pause isn't wrap-up, so no handoff documentation" | The resume may happen in a fresh session that cannot read this chat. Notes-in-chat = notes lost. |
| "Uncommitted changes aren't at risk just because processes stopped" | Shared checkouts get branch-switched, worktrees get pruned, days pass. Durability is cheap; loss is not. |
| "I'll remember where I was" | You won't exist. The notes file will. |
| "That process on the port is in the way" | If you didn't start it, it belongs to another session. Report it; never kill it. |

## Red flags — the pause is NOT done

- Final message sent without the banner.
- Dirty worktree and no `wip:` commit.
- Resume point exists only in the final chat message.
- A wakeup/cron still armed, or a sub-agent still running.
- You killed a process you couldn't match to your own records.
