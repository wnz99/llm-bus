# Coordinator-delegated approval gates and guided setup: summary

Five pull requests implement approved plan revision 6 (13 tasks).

PR #67 (Tasks 1-6): compatibility baseline; schema 13 and the delegation
model; approval directions, claims and gate authority; session banner and
guidance; guided setup; rules, skill and documentation updates.

PR #68 (Task 7): merge gate records, coordinator acceptance, auto-merge and
record-bound approvals. A review fix shows the record evidence to the
coordinator.

PR #69 (Tasks 8-9): objective amendments with a coordinate guard; standing
owner instructions with role-filtered display and verbatim injection at
SessionStart, compaction and recovery; rejection of delimiter and
format characters.

PR #70 (Tasks 10-11): owner decision records with provenance, worker
acceptance, timed scope defaults with rechecks, and a coordinator digest.

PR #71 (Tasks 12-13): relay approval transport with migration 0007. The
receiver peer capability `accept_delegated_gates` defaults to deny. The sender
is checked at enqueue and at upload. Only the receiver may amend a
remote-coordinated project. A review fix drops the capability when the peer is
outside inbox mode.

Owner decisions 1-28 are recorded in the drafts DISCOVERY.md, including C1
option B (D24), a single rollout after PR5 (D25) and the approved test-file
allowances (D26-D28).

Reviews: each task had an independent review, with fix loops where needed. Per
PR, planner conformance was CONFORMS and aggregate verification was PASS. Each
PR had independent PR review loops; the final rounds approved with Low/Nit
residuals only. CI was green on every PR. PRs #67-#70 are merged; PR #71 is
approved on 5228cef and its merge is pending. This summary commit changes only
tracking records.

Verification on 5228cef: `uv run poe check` passed (791 tests); the cloudflare
suite passed (49 tests).

Known limits and residuals:
- Receiver-local `setup`, `replace` and `amend` on remote-coordinated projects
  are guarded by rules, not code. They are capped by `accept_delegated_gates`
  and by the coordinator machine's delegation check.
- The decision class relies on coordinator compliance.
- The digest has no acknowledgement.
- Standing instructions survive `replace`; there is no clear command.
- Remote parties get no decision notices.
- Hosts may truncate long guidance.
- The PR5 inbox-mode runtime check is tested only indirectly.

Pending after merge (owner approval required for each step, in order):
1. Apply relay migration 0007.
2. Deploy the Worker.
3. Run `uv run llm-bus upgrade` from a clean main. The mailbox moves to schema
   13; older binaries refuse it.
4. Close agents, run `llm-bus install`, then restart agents and the bridge.
5. Copy `skills/llm-bus/` and `examples/instructions/llm-bus-coordination.md`
   into ~/.agents.

Canonical evidence (outside the repository):
~/.local/state/llm-bus/planning/coordinator-delegation/ LEDGER.md and the impl,
review, fix, conformance, verify and pr-review reports.
