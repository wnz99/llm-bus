# 261110-wft — Coordinator-delegated approval gates and guided setup

## Registration

Registered 2026-11-10 with the personal feature-workflow tracking-record fallback. No GSD tooling wrote this record. Tracking only.

Approved semantic revision: 4. Independent plan gate: cycle 1, round 2 APPROVED with three Low/Info residuals (N1, N2 for Task 13; N3 for Task 10). Owner approved the plan on 2026-11-10, with no sender-side per-peer cap on delegated gates.

Delivery: five sequential PRs (PR1 `feat/coordinator-delegation-gates`, PR2 `feat/coordination-gate-records`, PR3 `feat/coordination-amendments-instructions`, PR4 `feat/coordination-owner-decisions`, PR5 `feat/coordination-remote-gates`). This record is registered on the PR1 branch and updated on each later branch; the summary ships with the last PR.

Canonical private draft: /Users/davidfava/Projects/Personal/llm-bus/.planning/drafts/coordinator-delegation/PLAN.md

## Decisions

- Delegated gates: `commit`, `push+PR`, `merge`, `merge-admin`. `merge-admin` may bypass only the required-approvals rule; required CI must be green on the exact head SHA. Targets are explicit per gate (`--delegate merge:develop`); no implicit default.
- Delegation is set per project at coordinate time; each worker may narrow, never widen.
- The bus enforces structured parts (gate, target allowlist, SHA binding, expiry); the worker verifies CI and review evidence live before acting and refuses on mismatch.
- New verified direction operations (`approve-commit`, `approve-push`, `approve-merge`, `approve-merge-admin`) are verified like existing directions plus a covering delegated gate.
- Revoke, leave, expiry and coordinator replacement end delegation with the grant. Never-delegated gates still need direct owner approval.
- Gates accepted through a worker's own direct owner join count as owner approval for exactly those gates and conditions. Stricter repository rules still win.
- Merge gate record: the worker submits it (verdict, reviewer evidence, head SHA, required checks); the coordinator must accept it before auto-merge. No worker self-certification. `gate-accept` issues an ordinary `approve-merge`/`approve-merge-admin` approval bound to the record.
- Owner decision records are stored verbatim with source. Clarifications within the objective apply directly; objective or acceptance-criteria changes are dated amendments (`coordination amend`). `coordinate` fails rather than silently ignoring a differing `--objective`.
- Decision defaults: low-risk scope clarifications take the recommended option after 30 minutes and are reported; never for security, cost, data or irreversible actions. At the deadline the bus applies the default and wakes the coordinator and the blocked worker.
- Standing owner instructions: set only by direct owner command in the coordinator session; re-injected verbatim only at SessionStart, compaction and recovery hooks.
- Wrong-session guard: hook session banner; workers refuse approvals naming a ticket or branch they do not own; the bus rejects such approvals structurally.
- Guided setup: `llm-bus coordinate PROJECT --setup` prints the fullest-delegation default profile for the owner to review, narrow or disable; nothing is stored until confirmed.
- Fixed non-delegable floor: force-push, deploys/production changes, credential or secret changes, paid or live runs, merge with failing or pending CI.
- Worker join: the direct owner `join PROJECT` binds the agreed delegation automatically; `join --narrow` is optional. Narrowing amendments apply at once; widening needs the worker's owner to confirm or rejoin.
- Remote: relay and client support in PR5; the receiver's `accept_delegated_gates` peer policy defaults to deny; the coordinator machine also checks its own delegation before sending. No sender-side per-peer cap.
- Storage: additive forward migration to schema version 13; older binaries need not work against a migrated store.
- Backward compatibility (hard constraint): the base bus without coordination behaves and prints exactly as before; existing coordination projects without delegation keep working; JSON adds optional keys only.

## Discovery summary

Direction operations were fixed to `assign`, `reprioritize`, `clarify`, `pause`, `cancel` and `review`, and coordination rules said directions cannot grant permission, so every commit, push and merge gate needed a live owner turn even in unattended coordinated runs. Owner feedback from a coordinated project (basic-app-login) asked for delegable gates, a guided coordinator setup and automatic worker binding on join. Machine-policy rules require both sender and receiver to allow remote operations, with default deny. Out of scope: typed worker reports, lock queue fixes, scope pinning, staged start, worker reset, secret redaction, owner-action queue, status board and broadcast.
