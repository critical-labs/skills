# Critical Labs — Claude Code Skills

Reusable [Agent Skills](https://agentskills.io) for Claude Code: agent workflows and orchestration playbooks.

Install a skill by copying its directory into `~/.claude/skills/` (personal) or `.claude/skills/` (project).

## Skills

- [github-agent-team-orchestration](github-agent-team-orchestration/SKILL.md) — run an unattended team of worker agents against a GitHub issue backlog: bot-identity fork PRs, a separate technical-writer worker for reader-first PR descriptions, cross-review ring, maintainer-first PR babysitting, production uptime watch, and usage-limit-surviving supervision.
- [pause](pause/SKILL.md) — park a session on "pause all": halt workers, sub-agents, shells and wakeups, WIP-commit and write cold-session resume notes, then stamp the 10-line PAUSED banner.
