# Example PR description (writer output, test run on a real PR)

Title: `fix: one magic-link sender per process, one Sentry transaction per drain`

## Why

Members sign in with an emailed magic link. The web gateway queues each link, and a background sender drains the queue every 30 seconds. A drain claims the due links from the identity service and emails them. Issue #495 reports that one Node process runs several copies of that sender, because Next.js loads the module more than once. On the release candidate (RC) two senders ran side by side. Each copy paces its own sends, so the rate that protects our email reputation is not enforced. Each tick's claim and send spans also reach Sentry as orphans, with no parent transaction.

## What changes

- Nothing visible: members and admins still get their sign-in links.
- The maintainer sees one sender timer per gateway process, however often Next.js loads the module.
- The maintainer sees each drain in Sentry as one named transaction, so its claim and send spans have an owner.
- An operator gets the send rate enforced for the whole process, not per module copy.
- The admin gateway's sender, from PR #463, gets the same fix through the same shared code.

## How it works

A new shared package, the outbox sender, keeps the timer, the one-drain-at-a-time lock and the pacing clock in one object on the process-wide global. Every module copy sees the same object, so a process has one timer, one lock and one pacer. Both gateways call that package and keep only their own claim and delivery code. Each drain runs inside one Sentry span forced to be a transaction, and each timer tick starts a fresh trace. The decision to question is that every tick is traced, even an empty one. Skipping empty ticks would put the claim query back outside the span, as an orphan.

## How to check it

1. The new tests load the sender module twice, as Next.js does, and prove both copies share one interval, one lock and one pacing clock. They also prove each drain is one transaction and each tick opens its own trace.
2. After the RC deploy, open Sentry and look for one "magic-link drain" transaction every 30 seconds per process.
3. Confirm no orphan claim spans remain, and that one timer survives after both the login page and the session refresh endpoint have run.
4. Not verified: no deploy was run, and Sentry was only mocked in tests, never a real project.

## Risks and choices

- Every tick is traced, even an empty one, which the issue left open. The cost is Sentry quota; sampling can drop the drain transactions by name.
- Each tick starts its own trace; otherwise every tick would sit inside the trace of the request that first started the timer.
- The new package depends on Sentry's core library; the gateways gain no Sentry dependency.
- A drain that throws at once now still releases the lock, and the timer swallows a rejected drain; the sender logs its own failures.
- The mobile gateway's copy of the sender is untouched. It runs as a single instance, and the scope the maintainer accepted on the issue named only the two web gateways.
- This supersedes PR #738, the same change before main moved under it.

## For the reviewer

Read packages/outbox-sender/src/index.ts first, then services/web-gateway/src/lib/magic-link-sender.ts to see what each gateway still owns. The question: is a traced transaction every 30 seconds per process acceptable for our Sentry span budget, or should it be sampled down?

Closes #495
Supersedes #738
Code by agent worker w1; description by the team's technical writer agent from the author's handoff.

<details><summary>Test evidence and commit split</summary>

Local run by the implementer, rebuilt on main `0bd1607d`; CI on the PR is green (typecheck, unit tests, commits and lint, smoke e2e, Socket Security).

```
packages/outbox-sender      tsc ok; vitest 1 file / 11 tests pass
services/web-gateway        tsc ok; vitest 111 files / 1888 tests pass (incl. magic-link-sender.process.test.ts)
services/admin-web-gateway  tsc ok; vitest 8 files / 90 tests pass (incl. magic-link-sender.process.test.ts)
clients/web-client, platforms/digital-ocean-inprocess-postgres, platforms/github-actions/admin-web-gateway: tsc ok
biome lint (changed files): clean
npm install with the merged lockfile: lockfile unchanged
```

Red first (from #738, code unchanged): both gateways' two-instance tests failed against the old module-level state with 2 intervals not 1, a second drain promise, 0 ms between two instances' sends, and a reset that left the other instance's timer running.

Package tests (`packages/outbox-sender/src/index.test.ts`, 11): one interval across instances; shared lock with look-again; pacing across instances (fake timers, 500 ms at 2/s); reset clears everything; separate keys stay independent; drain on start then every interval; `detach` wraps the timer and the drain; lock released on a sync or async throw; one `queue.process` span with `forceTransaction: true` per drain, look-agains included; a new trace per timer tick (`startNewTrace`).

Gateway tests (4 each, `magic-link-sender.process.test.ts`): two instances share one interval, one lock (second returns the first's promise), one pace (web: `MAGIC_LINK_SEND_RATE`; admin: 2/s), and `resetMagicLinkSender()` clears all of it from either instance.

| Commit | Scope | Files |
| --- | --- | --- |
| `ef8b9af2` | feat(repo) | `packages/outbox-sender/` (package.json, tsconfig.json, src/index.ts +117, src/index.test.ts +179), `package-lock.json` +18 |
| `5aa77244` | fix(web-gateway) | `src/lib/magic-link-sender.ts` +30/-51, `src/lib/magic-link-sender.process.test.ts` +114, package.json +1 |
| `7eeb0637` | fix(admin-web-gateway) | `src/lib/magic-link-sender.ts` +20/-42, `src/lib/magic-link-sender.process.test.ts` +96, package.json +1 |

Head: `7eeb063764644bf7d624195c18db111d41e6d195` on `agent/w1-issue-495-v2` (matches the brief). No migrations. Shared state key: `Symbol.for('homefree.outbox-sender:<key>')`; keys `web-gateway magic-link sender` and `admin-web-gateway magic-link sender`; span names `magic-link drain` and `admin magic-link drain`. New dependency: `@sentry/core ^10.56.0` on the package only.

</details>
