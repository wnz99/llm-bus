# coordinator-delegation — Coordinator-delegated approval gates and guided setup

Plan revision 6 (2026-11-11). Requirements: `.planning/drafts/coordinator-delegation/DISCOVERY.md`
(decisions D1–D25 below refer to its numbered user decisions). Background:
`~/.local/state/llm-bus/planning/coordinator-delegation/source-feedback.md`.
Revision 2 applied owner answers D17–D20: additive schema migration to version 13
(no lazy tables), PR5 kept, standing instructions injected only in full recovery
output, and coordinator acceptance of worker-submitted merge gate records.
Revision 3 applies owner simplicity decisions D21–D23
(`~/.local/state/llm-bus/planning/coordinator-delegation/simplicity-r1.md`):
gate acceptance sends an ordinary record-bound merge approval (no `gate-ready`);
the decision-default timer stays and wakes the coordinator and the blocked worker
at the deadline; decision `kind` merged into `class`; one decision-entry action;
one entry point for standing instructions; no `--verdict` flag; one owner of the
rejoin-required computation.
Revision 4 applies plan review C1R1
(`~/.local/state/llm-bus/planning/coordinator-delegation/plan-review-c1r1.md`):
the coordinator machine checks its own project delegation before a remote
approval is enqueued and again before upload (sender side of the AGENTS.md
sender-and-receiver rule); a worker that narrowed away `decision-defaults`
never sees a defaulted decision as accepted; coordinator guidance again applies
due decision defaults lazily, as revision 2 did and as the lock hook path
already does; plus five low-risk fixes (claim uniqueness, D19 Codex wording,
decision-timer project lookup, wizard branch hint, decision-class risk).
Revision 5 fits Tasks 7–13 to the PR1 code as delivered (Tasks 1–6, branch
`feat/coordinator-delegation-gates` at `0fec966`; findings R1–R5 in
`~/.local/state/llm-bus/planning/coordinator-delegation/conformance-pr1-a1.md`).
Tasks 1–6 are not replanned; only stale text about them is corrected. New in
revision 5: "Shared contracts for PR2–PR5" before Task 7 (delegation flags,
`amend`/resume flag semantics, action results and wakes, lazy default seam);
Task 13 keeps PR1's branch check against the project's targets. Owner answers
to revision 5 (D24, D25): a delegation or binding is valid with at least one
item, gate or flag, so `owner-decisions` may exist without a gate (C1 option B,
Task 10 and SC1/SC2); the installed tool, hooks, rules and skill on this
machine are upgraded once, after PR5 merges ("Post-merge owner actions").
Revision 6 applies plan review C2R1
(`~/.local/state/llm-bus/planning/coordinator-delegation/plan-review-c2r1.md`):
`decisions.apply_due` acts only on current-generation rows while the current
project delegation still enables decision defaults (M1, Task 11); the receiver
owner of a remote-coordinated project re-declares or narrows its own gate
delegation with `coordination amend`, including after `replace` (M2, Task 13);
wizard flag words never reach `from_specs` (L1, SC2); the sender gate check
sits on the upload path only (L2, Task 13); a gate record supersedes only rows
for the same PR and branch (L3, Task 7); stale signatures corrected (L4).

## Features and effects

Today an owner running a coordinated project must type every commit, push, PR
and merge approval into each worker session, because coordination directions
can never carry permission. This flow lets the owner delegate a fixed set of
approval gates once, at coordinate time, and lets each worker's own owner bind
that delegation through the existing direct `join`. The coordinator can then
send structured approval directions that the receiving bus verifies against
both owners' choices, explicit target branches, an expiry and the exact head
SHA. Workers still check CI and review evidence live before acting. Later
phases add a merge gate record (submitted by the worker; the coordinator's
acceptance sends the worker an ordinary merge approval bound to that record)
that lets a worker merge once CI is green without another coordinator turn,
owner decision records with timed defaults that wake the waiting sessions, objective
amendments, standing owner instructions restored at session start and
recovery, and the same delegated gates for remote workers under the receiving
machine's peer policy.

The base bus (send, inbox, ack, lock, hooks) and every existing coordination
project without delegation keep working and printing as today. Storage grows by
one additive forward migration to schema version 13 that creates empty tables;
no existing table or row is rewritten or dropped.

### F1: Delegated approval gates (local)

- Problem and current behavior: `OPERATIONS` in `src/llm_bus/coordination/models.py`
  has six work-direction operations. None can approve a commit, push or merge,
  and the rules forbid workers from treating relayed approval as permission.
- Proposed behavior: `llm-bus coordinate PROJECT --delegate commit:develop
  --delegate merge-admin:develop ...` stores the delegation for the current
  coordinator generation. A worker's direct `join PROJECT` binds that delegation
  to its grant (optionally narrowed with `--narrow`). The coordinator runs
  `llm-bus coordination approve PROJECT --recipient W --gate merge-admin
  --ticket T --branch B --base develop --pr 42 --head-sha SHA`. The receiving bus
  verifies it like any direction, plus: the gate is in both the project
  delegation and the worker binding, the base branch is an explicit target, the
  work branch is not a target branch, the worker's claimed ticket and branch
  match, and the approval is unexpired. Verified approvals count as "the user
  asked" for exactly that gate and those conditions (D6). Never-delegated gates
  and the fixed floor (D13) still need direct owner approval.
- Effect on the code: schema version 13 migration (`mailbox/schema.py`,
  `coordination/store.py:create_delegation_tables`); new `coordination/delegation.py`
  (model, binding); `Approval` model and gate operations in `models.py`; gate
  branch in `authority.authorize`; approval persistence in `delivery.py`;
  `approve`, `claim` and `amend` actions in `coordination/cli.py`; approval
  threading in the send path.
- Scope and compatibility: local projects only in PR1; remote approvals are
  rejected with explicit errors until F7. Existing six operations, grant JSON,
  direction wire format and relay contract are unchanged. Delegation ends with
  the grant, generation or project (D5) because it is keyed by both.
- Acceptance outcomes: delegated and bound gate verifies; every other combination
  (not delegated, not bound, narrowed away, wrong base, branch equals a target,
  wrong ticket or branch, expired, revoked, replaced, left, remote) is denied
  with a reason and hidden from the inbox; existing projects behave unchanged;
  a version 12 store upgrades to 13 with all rows intact.
- Execution mapping: Tasks 2, 3.

### F2: Wrong-session guard and session banner

- Problem and current behavior: owners pasted approvals into the wrong worker
  session; hooks show only the project label, role and state.
- Proposed behavior: workers record their ticket and branch with
  `llm-bus coordination claim PROJECT --ticket T --branch B`. Every hook for an
  enrolled session prints a one-line banner (project, role, label, ticket,
  branch, folder). Worker guidance requires prefixing owner-confirmation requests
  with the banner and refusing owner approvals naming another ticket or branch.
  The bus structurally rejects approval directions whose ticket or branch does
  not match the worker's claim, at send time and at every exposure (D11).
- Effect on the code: `coordination_claims` table (version 13), banner and rule
  text in `cli/guidance.py`.
- Scope and compatibility: banner appears only for enrolled sessions; the
  unenrolled hook output is unchanged.
- Acceptance outcomes: banner present in full and compact hook output for an
  enrolled worker; absent for unenrolled sessions; mismatched approvals rejected.
- Execution mapping: Tasks 3, 4.

### F3: Guided coordinator setup

- Problem and current behavior: the owner must know every flag.
- Proposed behavior: `llm-bus coordinate PROJECT --setup` stores nothing. In an
  agent (non-TTY) it prints JSON with the default profile (all delegable items
  enabled, targets pre-filled with the detected integration branch), the fixed
  floor, review steps and a command template. The coordinating agent asks the
  owner through its native question UI and runs `coordinate` with the confirmed
  flags. In a real terminal it runs a prompt wizard with the same profile and a
  review screen; the owner may disable items or change targets to other explicit
  branch names, never add floor items; nothing is stored until the owner types
  `yes` (D12).
- Effect on the code: new `coordination/setup.py`; `--setup` routing in
  `cli/app.py` before host identity is required.
- Scope and compatibility: each later PR adds its own toggle to the profile.
- Acceptance outcomes: non-TTY output stores nothing; wizard creates the project
  only after confirmation; detected branch pre-filled; floor listed.
- Execution mapping: Task 5 (PR1); Tasks 7 and 11 extend the profile; Task 9
  adds the optional `coordination instructions` follow-up step.

### F4: Merge gate record, coordinator acceptance and auto-merge

- Problem and current behavior: each merge needs a coordinator turn to check
  verdict, SHA and CI by hand, and then another turn to send the approval.
- Proposed behavior (D7, D20, D21):
  1. The worker that claims the branch submits a record with
     `coordination gate-record` (review evidence references, head SHA, required
     check names; submitting asserts the `approved` verdict). The record starts
     as `submitted`, and the bus sends the coordinator a plain notice message.
  2. The coordinator verifies the record and the PR live (Task 7 docs), then
     runs `coordination gate-accept PROJECT --record-id ID --head-sha SHA
     [--admin]`. In one transaction the bus checks structure and current
     authority, marks the record `accepted`, and sends the submitter an ordinary
     `approve-merge` (or, with `--admin`, `approve-merge-admin`) approval
     direction through the existing send path. The approval carries the
     record's ticket, branch, base, PR and head SHA, a 24 hour expiry and the
     optional key `record_id`. `gate-reject` withdraws a submitted or accepted
     record.
  3. The worker acts only on that verified inbox approval: it waits for required
     CI to pass on that SHA, runs the Task 6 merge live checks and merges with
     `gh pr merge --match-head-commit SHA`, without another coordinator turn.
  At send time and at every exposure the bus denies an approval that carries
  `record_id` unless that record is still `accepted` in the current generation
  and matches the approval (`record_withdrawn`). Rejection or a resubmission
  (which marks older records `superseded`) therefore hides an approval that was
  already sent. No session can both submit and accept: only an enrolled worker
  submits and only the current enrolled coordinator accepts, and one address
  has one enrollment.
- Effect on the code: `coordination/gate_records.py`; `coordination_gate_records`
  table (version 13); delegation key `auto_merge`; optional `Approval.record_id`
  and one record-status check in `authority._gate_decision`.
- Acceptance outcomes: the approval exists only after coordinator acceptance; a
  worker cannot accept, and a coordinator cannot submit; acceptance fails for a
  stale generation, an expired or non-submitted record, a different SHA, a
  missing `auto-merge` item or any gate-authority denial (changed claim, missing
  gate); rejection or a new submission for the same PR makes the sent approval
  `verified: false`; a submitted record can be accepted for up to 24 hours, and
  the approval's own `expires_at` is its only later expiry.
- Execution mapping: Task 7 (PR2).

### F5: Objective amendments and standing owner instructions

- Problem and current behavior: `coordinate` on an existing project silently
  ignores a different `--objective`; owner rules are lost at compaction.
- Proposed behavior: `coordination amend PROJECT --objective TEXT` appends a dated
  amendment; `coordinate` fails when `--objective` differs from the effective
  objective (D8). `coordination instructions PROJECT` sets or amends owner
  standing instructions (coordinator and optional worker audience) with hash and
  timestamp, shown by `coordination show`. Their text is injected verbatim only
  in the full recovery form of the coordination hook block: SessionStart (every
  source, including Claude Code `compact`), the first hook after missing or
  outdated guidance state (the Codex recovery notice), and the
  `llm-bus guidance` recovery command used after compaction (D10, D19). Compact
  prompt reminders carry only a one-line pointer with the hash prefix and the
  recovery command, never the text. Codex has no compaction hook
  (`hosts/hooks.py:EVENTS`), so the plan meets D19's Codex clause with that
  pointer line plus the `llm-bus guidance` recovery command, and with the full
  output after missing or outdated guidance state; it cannot inject
  automatically after a Codex compaction.
- Effect on the code: `coordination/amendments.py`, `coordination/instructions.py`,
  guidance injection gated on `full`.
- Acceptance outcomes: amendment history retained; differing objective rejected;
  instructions verbatim in SessionStart, compact-source SessionStart, outdated
  guidance state and `llm-bus guidance` output; absent from compact
  UserPromptSubmit output except for the pointer line.
- Execution mapping: Tasks 8, 9 (PR3).

### F6: Owner decision records and timed defaults

- Problem and current behavior: owner answers given in the coordinator session
  cannot reach workers; decisions stall while the owner is away.
- Proposed behavior: the coordinator records questions and owner answers with
  `coordination decision-ask` (with `--answer` when the owner already answered)
  and `decision-answer`. The bus stores the answer verbatim with session, turn,
  time and SHA-256. Workers that bound the `owner-decisions` item accept a
  decision after `coordination decision-show` reports `accepted: true`.
  `objective`-class decisions append an objective amendment when answered. With
  `decision-defaults` enabled, an unanswered `scope`-class decision takes the
  recommended option at its deadline (default 30 minutes), never for
  `security`, `cost`, `data`, `irreversible` or `objective` classes. At the
  deadline a detached timer applies the default and sends plain notices that
  wake both the coordinator and the blocked worker named by `--worker` through
  the existing wake mechanism (D22). If the timer did not run, the next
  coordination CLI action or coordinator hook applies the due default lazily
  with the same notices and wakes, as hooks already do for due lock checks.
  A worker whose owner narrowed away `decision-defaults` never treats a
  defaulted decision as accepted; it waits for an owner answer. The decision
  appears in the per-generation digest (D8, D9).
- Effect on the code: `coordination/decisions.py`, a coordinator turn counter in
  the hook, a detached timer reusing the lock-timer pattern, lazy application in
  coordinator guidance, and `locks.notices.deliver` for wakes.
- Acceptance outcomes: provenance fields stored; ineligible classes never
  default; defaults applied once by the timer, a CLI action or a coordinator
  hook; at the deadline the coordinator and the named worker each receive a
  notice and a wake attempt; a worker narrowed away from `decision-defaults`
  gets `accepted: false` for a defaulted decision; digest lists them.
- Execution mapping: Tasks 10, 11 (PR4).

### F7: Remote delegated gates

- Problem and current behavior: remote workers receive directions through the
  relay, which accepts only the six operations and no approval data.
- Proposed behavior: the receiver owner imports the binding with `coordination
  setup ... --delegate GATE:BRANCH`; its local peer policy key
  `accept_delegated_gates` (default empty, deny) caps which gates a remote
  coordinator may use. Approvals travel in a new `approval` envelope key over the
  existing coordination route after capability negotiation. Effective gates are
  local project delegation ∩ worker binding ∩ peer policy cap (D14, D18).
  The sender side also must allow (AGENTS.md): the coordinator machine checks
  the approval against its own owner's project delegation (gate, base target,
  branch not a target, expiry) before enqueueing it and again before each
  upload, so a later `amend` that narrows the delegation holds a queued
  approval on the coordinator machine.
  The receiver owner keeps control of its own gate delegation for the whole
  project life: `coordination amend PROJECT --delegate GATE:BRANCH` (or
  `--no-delegation`) on a remote-coordinated project re-declares it after a
  local `replace` (a new generation starts without one, D5) and narrows it
  at once mid-generation (D14). No local session is the coordinator there, so
  this is the same receiver-owner authority as `setup` and `replace`.
- Effect on the code: relay `messages.ts` and migration `0007`, client protocol,
  outbox, outgoing, incoming, admission, policy, and a shared delegation check
  in `authority.py`.
- Acceptance outcomes: remote approval verified only when all three receiver
  checks allow and the coordinator's own delegation covered it when it left;
  a coordinator delegation without the gate or target refuses the send; a
  delegation narrowed after enqueue holds the item; old relays leave approvals
  queued with `unsupported_approvals`; denied peers never reach the inbox.
- Execution mapping: Tasks 12, 13 (PR5).

### F8: Rules, skill and documentation

- Proposed behavior: the in-repo rule file, skill reference, README and examples
  describe delegation, D6 host-rule semantics, the floor, live verification
  commands, gate record acceptance and the banner rule. Installed copies outside
  the repository are updated only by the owner-approved copy after PR5 merges
  (D25).
- Execution mapping: Task 6 (PR1); Tasks 7, 9, 11, 13 fold their own docs.

### F9: Backward-compatibility proof

- Proposed behavior: a frozen base-bus golden test and a legacy-project test are
  added before any change and must pass unchanged in every PR. A migration test
  proves a version 12 store upgrades to 13 with every existing row intact. The
  only existing-test edit in the whole flow is the single
  `user_version == 12` literal in
  `tests/coordination/test_availability.py:test_additive_schema_upgrade`, which
  becomes `13` (D15, D17).
- Execution mapping: Task 1; Task 2 (migration and literal); every task gate.

## High-level design and rationale

Authority stays receiver-derived. A delegation is owner data in two places: the
project delegation (coordinator owner, per generation) and the grant binding
(worker owner, per grant). The effective set is their intersection, so project
narrowing applies at once and widening has no effect until the worker's owner
runs `join` again in that session (D14). Gates and the floor are closed
enumerations, so a floor item cannot be represented. A gate approval is a normal
direction whose operation is one of `approve-commit`, `approve-push`,
`approve-merge`, `approve-merge-admin` (one per gate, D4), plus a separately
stored `Approval` record. `authorize` keeps its six-operation path byte for byte
and adds one gate branch. Merge gate records reuse the same gate branch: an
accepted record produces an ordinary stored approval, and the gate branch adds
one check that the record behind it is still accepted (D21).

- Design choices:
  - Approval data lives beside `Direction`, not inside it. Existing tests and the
    relay build wire items with `asdict(Direction)` and require exactly five keys
    (`tests/remote/test_coordination.py`, `cloudflare/src/messages.ts:direction`);
    adding a field would break both.
  - Gate targets name the base (integration) branch for every gate
    (`--delegate GATE:BRANCH`, exact names, no globs). Commit and push act on the
    worker's claimed work branch, which must differ from every target. This
    matches D12's single pre-filled integration branch and D1's explicit targets.
  - Delegation is keyed by generation, so replacement ends it (D5) without new
    lifecycle code; revoke, leave and expiry end it through the existing grant
    checks that run before the gate branch.
  - Storage (D17): one additive forward migration. `SCHEMA_VERSION` becomes 13,
    and a new `DELEGATION_SCHEMA_VERSION = 13` step in
    `mailbox/schema.py:ensure_schema` calls
    `coordination/store.py:create_delegation_tables`, which creates all nine
    tables this flow uses with `CREATE TABLE IF NOT EXISTS`. This follows the
    existing precedent: version 12 already creates the coordination tables in
    every store, including stores where coordination is never used. All DDL
    lands in PR1, so every later PR is code-only and one store version covers
    the flow. Readers never check whether a table exists. Rejected: per-PR
    version bumps (13 to 16), which contradict D17's single version 13 and would
    edit the same test literal four times.
  - Merge gate trust (D20): the worker submits, the coordinator accepts, and the
    bus refuses both self-certification paths structurally. Acceptance is the
    coordinator's single turn per merge and is itself the merge approval (D21);
    it can happen before CI finishes, so the worker still merges without a
    further coordinator turn once CI is green (D7). Workers learn one way to
    receive merge permission, and one expiry model applies. Rejected: a
    separate readiness query, which duplicated the approval path.
  - Decision defaults (D22): a detached timer per defaultable decision, reusing
    the lock-timer pattern, so the deadline wakes the coordinator and the
    blocked worker even when no session reads the decision. If the timer is
    lost (sleep, reboot, fork failure), coordination CLI actions and the
    coordinator's hook guidance apply due defaults lazily. This is the lock
    precedent: `locks/timer.py:schedule_check` pairs the timer with "the next
    lock command or hook", and `locks/summary.py:summary` already reaps due
    checks and calls `locks.notices.deliver` from every hook. The
    `status='pending'` guard in `BEGIN IMMEDIATE` keeps application single.
  - Standing instructions (D19) are injected only when guidance is emitted in
    full form. That one condition (`full=True` in `cli/guidance.py:_coordination`)
    already covers SessionStart for every source, missing or outdated guidance
    state, and `llm-bus guidance`. Compact reminders get a pointer line so a
    Codex session, which has no compaction hook, knows to run the recovery
    command.
  - The bus enforces structure; the worker verifies CI and review evidence live
    with `gh` (D3). The bus makes no network calls.
  - Approvals are sent by a dedicated `coordination approve` action, so `send`
    flags and help stay unchanged.
  - Records, decisions, instructions and auto-merge are local-machine data: a
    record stored on another machine cannot speak for the receiving machine's
    owner (AGENTS.md decentralized rules). Remote workers get delegated gates
    only (F7).
- Risks and safeguards:
  - Owner provenance is procedural: the bus cannot prove the owner, not the
    agent, ran `coordinate`, `amend`, `instructions`, `decision-answer` or
    `gate-accept`. This matches the existing trust model for `coordinate` and
    `join` (`skills/llm-bus/references/coordination.md`, "Processes sharing an OS
    user are procedurally trusted"). Owner-only actions require the caller to be
    the current enrolled coordinator.
  - Gate acceptance is only as good as the coordinator's live check. Safeguard:
    acceptance binds the exact head SHA the coordinator supplies; any new push
    needs a new submission and a new acceptance; the worker still checks CI live
    before merging; the floor still forbids merge with failing or pending CI.
  - Schema version 13 is a one-way step (D16 accepts this). Once a PR1 binary
    opens a store, older binaries refuse it with "Mailbox schema is newer than
    this llm-bus version". Rolling back the CLI therefore needs a binary at
    version 13 or later. `bus-upgrade` verification runs `upgrade --help`, which
    never opens the store, so its own rollback is unaffected.
  - All DDL for PR2–PR4 is fixed in PR1. A later column change needs an
    additive version 14 migration. Safeguard: Task 2 review covers every table
    against Tasks 7–11 interfaces before PR1 merges. If later PRs are abandoned,
    their tables stay empty and inert.
  - The coordinator agent chooses the decision class, so it could file a
    security, cost or data question as `scope` and make it defaultable (D9).
    Safeguard: the rule text tells coordinators to classify conservatively and
    use a non-default class when unsure; defaults are listed in the digest for
    the owner; the bus cannot judge question content.
  - Codex has no compaction event (README "No other compaction event is
    assumed"). After a Codex compaction, standing instructions return only
    when the agent runs `llm-bus guidance`, which the compact pointer line and
    the existing rule file (`examples/instructions/llm-bus.md`) instruct.
- Open decisions: none. C1 is decided (D24, option B): a project delegation
  or worker binding is valid when it holds at least one item, gate or flag.
  The fixed floor is unchanged. Planner-resolved choices a reviewer may
  challenge are listed in the planner reports.
- Version skew (D16): from PR2 on, a stored delegation can carry optional flag
  keys. A PR1 binary's strict `Delegation.parse` rejects them, so every session
  on that machine must run a binary of the same PR or later, as for schema 13.

## Design diagrams

Approval authority (F1, F2). Both owner actions feed the effective set; the
receiver checks it at send time and at every exposure.

```text
[owner in coordinator session]          [owner in worker session]
  coordinate --delegate GATE:BRANCH       join PROJECT [--narrow ITEM]
              |                                       |
              v                                       v
  [project delegation, per generation]   [grant binding, per worker grant]
              |                                       |
              +------------------+--------------------+
                                 | effective = project AND binding
                                 v
[coordinator] coordination approve ---> [direction + approval record]
                                                  |
                                                  v
                          [authorize: grant checks + gate_decision]
                           gate covers op, base in targets, branch
                           is not a target, worker claim matches,
                           approval not expired, coordinator local
                                                  |
                         denied                   |          verified
              [hidden; history keeps] <-----------+----------> [inbox]
                                                                  |
                                                                  v
                                   [worker: live CI and review check on
                                    the exact SHA; repo rules; act or
                                    refuse; ack]
```

Merge gate record lifecycle (F4). Only the named role can take each transition.

```text
 worker: gate-record          coordinator: gate-accept --head-sha SHA [--admin]
        |                                |
        v                                v
  [submitted] ----------------------> [accepted] --same transaction--> approve-merge(-admin)
     |    \                              |                             direction to worker,
     |     \ new gate-record for PR      |                             record_id, expires 24 h
     |      +---> [superseded] <---------+                                     |
     |                                   |                                     v
     | 24 h without acceptance           | gate-reject           worker inbox: verified only
     v                                   v                       while record is accepted
  [expired: accept refused]          [rejected]                   (+ live CI green)
                                                                               |
 coordinator: gate-reject also moves [submitted] to [rejected]                 v
                                                          gh pr merge --match-head-commit SHA
```

Leaving `accepted` (superseded or rejected) makes the already sent approval
`verified: false` at its next exposure (`record_withdrawn`). The approval's own
`expires_at` is the only expiry after acceptance.

PR dependencies. Arrows mean "requires merged".

```text
bus-upgrade (sibling flow) ---> PR1 core local gates + schema v13
                                   |
                                   +---> PR2 gate record + acceptance + auto-merge
                                   |
                                   +---> PR3 amendments + standing instructions
                                   |               |
                                   |               v
                                   |      PR4 owner decisions + timed defaults
                                   |
                                   +---> PR5 remote delegated gates

Merge order (sequential, shared files): bus-upgrade, PR1, PR2, PR3, PR4, PR5
```

## Data-flow analysis

| Stage | Input and source | Processing and owner | Output or side effect | Material failure handling |
| --- | --- | --- | --- | --- |
| Migrate | Store at `user_version` ≤ 12 opened by a PR1 binary | `ensure_schema` in `BEGIN IMMEDIATE`; `create_delegation_tables` | Nine empty tables; `user_version = 13`; existing rows untouched | Any error rolls back the whole migration; store stays at its old version |
| Delegate | `--delegate GATE:BRANCH` from coordinator owner command | `Delegation.from_specs` validates gate enum, branch regex, no duplicates; `delegation.set_project_delegation` | `coordination_delegations` row for (scope, project, generation) | Unknown gate, bad branch, floor word, differing spec on resume: `BusError`, nothing stored |
| Bind | Worker owner `join PROJECT [--narrow ITEM]` | `delegation.bind` copies project delegation, keeps only `--narrow` items | `coordination_grant_delegations` row per grant; join output lists set and floor | Narrow item not in project: `BusError` (cannot widen); no project delegation: no row |
| Claim | Worker `coordination claim --ticket --branch` | Enrolled-worker check; bounded text | `coordination_claims` row | Not enrolled: `BusError` |
| Approve | Coordinator `coordination approve` | `Approval` validated per operation; `outgoing` resolves target; `send_in_transaction` calls `authorize(..., approval=...)` | Message + `coordination_messages` + `coordination_approvals` rows in one transaction; wake | Any gate denial: `BusError("Direction denied: REASON")`, nothing stored |
| Expose | inbox, pending count, wake | `delivery.verification` reloads approval and reauthorizes with current state | Inbox `coordination` adds `approval` object for gate ops only | Denied: `verified: false`, hidden (existing `exposure_allowed`) |
| Act | Verified approval in worker inbox | Worker runs `gh`/`git` checks per skill reference | Commit, push+PR, or merge with `--match-head-commit` | Mismatch: worker refuses and reports to coordinator |
| Gate submit (F4) | Worker `coordination gate-record` | `gate_records.submit`: enrolled worker, claim match, auto-merge and covering gate bound; supersedes older rows for that PR | `submitted` row; plain notice message to coordinator | Coordinator caller, claim mismatch, no delegation: `BusError`, nothing stored |
| Gate accept (F4) | Coordinator `coordination gate-accept --record-id --head-sha [--admin]` | `gate_records.accept`: current coordinator, current generation, `submitted`, unexpired, SHA equal, `auto_merge` in project delegation and submitter binding; then `send_in_transaction(..., direction=, approval=)` runs the normal `authorize` gate branch | `accepted` row; message + `coordination_messages` + `coordination_approvals` rows (approval carries `record_id`) in the same transaction; wake after commit | Record checks: `BusError("Gate record not accepted: REASON")`; authority denial: `BusError("Direction denied: REASON")`; either rolls back, row stays `submitted` |
| Expose record-bound approval (F4) | inbox, pending count, wake | `_gate_decision` loads the record named by `record_id` | Verified only while the record is `accepted`, current generation, and matches ticket, branch, base, PR and SHA | `verified: false`, reason `record_withdrawn`; hidden |
| Decision default (F6) | Detached timer at `deadline_at`, or a later coordination CLI action or coordinator hook | `decisions.apply_due` in `BEGIN IMMEDIATE`, guarded by `status='pending'` | `defaulted` row; plain notice to coordinator and to the named worker; `locks.notices.deliver` dispatches both wakes after commit | Already answered or defaulted: no change, no notice; worker no longer enrolled: coordinator notice only; wake failure reported as `unknown`, message stays pending |
| Instructions (F5) | Hook event or `llm-bus guidance` | `_coordination(full=...)`: verbatim block when `full`, pointer line otherwise | Hook `additionalContext` or stdout | No active instructions: no block, no pointer |
| Remote send (F7) | Coordinator `coordination approve` to a `MACHINE/REPO/NATIVE` recipient | Coordinator machine: `outgoing` then `authority.sender_gate_reason` against its own project delegation at enqueue; `outgoing.upload` repeats it before each upload (not on the status path) | Queued envelope with `approval`; uploaded only while the check passes | Denied at enqueue: `BusError("Direction denied: REASON")`, nothing queued; denied later: held queued, never uploaded |
| Remote receive (F7) | Relay envelope `coordination` + `approval` | Relay validates shape; receiver `admission` parses, `authorize` with peer cap | Same as Expose | Old relay: `unsupported_approvals`, stays queued; peer cap deny: not admitted |

Example inbox entry for a verified approval (only the `approval` key is new):

```json
{"coordination": {"version": 1, "project_id": "…", "generation": "…", "grant_id": "…",
  "operation": "approve-merge-admin", "verified": true, "reason": "verified",
  "approval": {"ticket": "roam-22m", "branch": "roam-22m-login", "base": "develop",
    "pr": 42, "head_sha": "<40 hex>", "expires_at": "2026-11-10T16:00:00+00:00"}}}
```

## Execution instructions

**Tech stack:** Python 3.12 (`uv`, pytest, ruff, basedpyright strict, pylint,
deptry via `uv run poe check`), SQLite, argparse; TypeScript Cloudflare Worker
with D1 and vitest (`cloudflare/`, `bun run test`, `bun run check`); `git` and
`gh` used by workers and coordinators at runtime only, never by the bus.

### Global constraints

- AGENTS.md: "Configuration and trust decisions belong to each machine's owner." "A peer cannot expand another machine's permissions. Sender and receiver permissions must both allow the operation. New peer permissions default to deny; never infer trust from repository membership, machine names in message bodies, or an omitted setting."
- AGENTS.md: "Enforce local restrictions before agent inbox admission, pending-message exposure, and host wake." "Monitor text and reports must not enter automatic agent prompts or wake notices."
- AGENTS.md: "Changes to remote access must verify local-policy enforcement, denied-peer bypass attempts, and isolation of monitor/report data from agent delivery."
- D15 verbatim: "base bus without coordination behaves and prints exactly as today; existing coordination projects with no delegation keep working; existing ops keep semantics; new ops rejected without a grant; storage changes are additive forward migrations only (no rewrites/drops), existing projects and grants stay valid without rejoin; JSON adds optional keys only; existing test suite passes unchanged plus a regression test of base send/inbox/ack/lock with coordination never initialized."
- D17 verbatim: "normal additive forward migration to schema version 13 creating the new tables; update the single `user_version == 12` assertion to 13. Decision 15 means behaviour compatibility, not frozen test text; no lazy/second schema path."
- D19 verbatim: "only at SessionStart, compaction and recovery hooks (on Codex, also when the recovery notice fires); not on every coordination hook."
- D20 verbatim: "the worker submits the merge gate record; the coordinator must mark it accepted before auto-merge is allowed. No worker self-certification."
- D21 verbatim: "`gate-accept` issues an ordinary `approve-merge`/`approve-merge-admin` approval bound to the record id; drop `gate-ready`/readiness and its separate expiry; approval denied once the record is rejected or superseded."
- D22 verbatim: "keep the decision-default timer. At the deadline the bus applies the default and wakes both the coordinator and the blocked worker (no-idle rule)."
- D23: simplicity proposals P3–P7 apply as written in `simplicity-r1.md` (decision `kind` merged into `class`; `decision-ask --answer` replaces `decision-record`; instructions set only through `coordination instructions`; no `--verdict` flag; `delegation.rejoin_required` is the single owner of the narrower-binding computation).
- D13 floor, never delegable: force-push, deploys/production changes, credential or secret changes, paid or live runs, merge with failing or pending CI.
- D1: `merge-admin` may bypass only the required-approvals rule; required CI green on the exact head SHA. Targets explicit per gate; no implicit default.
- Out of scope (binding): typed worker reports/Stop hook, lock queue fixes, scope pinning, staged start, worker reset, secret redaction, owner-action queue, status board, broadcast.
- Repository: `uv` only; modules in `src/llm_bus/`, tests in `tests/`; `uv run poe check` before pushing; Conventional Commits; no `Co-Authored-By` trailers; PRs into `main`, merge commits, delete branches.
- Existing-test rule: the only modified pre-flow test line is the D17 literal. `TESTS_DIFF` below names the check every task gate runs:
  - `TESTS_DIFF` = `git diff --name-only --diff-filter=MD origin/main...HEAD -- tests/`
  - PR1 expected output: exactly `tests/coordination/test_availability.py`, and
    `git diff -U0 origin/main...HEAD -- tests/coordination/test_availability.py | grep '^[-+] '`
    prints exactly one `-` line ending `== 12` and one `+` line ending `== 13`.
  - PR2 expected output (amended by D26): at most
    `tests/coordination/test_setup.py` (PR1's own feature test).
  - PR3–PR4 expected output: empty. PR5 expected output: at most
    `tests/coordination/test_approvals.py` (a file added by PR1 in this flow).
- Bus code makes no network calls; hooks never run `gh`, Beads or network.
- Sibling flow `bus-upgrade` (revision 3) lands first; PR1 is cut from `main` after it merges. Overlap is limited to `src/llm_bus/cli/app.py:main`: bus-upgrade adds an `upgrade` dispatch before `Store(...)`; this flow adds the `coordinate --setup` route before `host_identity` (Task 5) and the `coordination approve` route inside the coordination branch (Task 3). bus-upgrade's `cli/parser.py:build_parser` edit (new `upgrade` subparser) does not overlap: this flow adds no top-level command and edits only `coordination/cli.py:add_parsers`. Neither flow touches the schema. On a textual conflict, keep both branches; `uv run poe check` and `uv run llm-bus upgrade --help` must pass afterwards.

### Acceptance criteria traceability

| Feature and requirement | Task(s) | Verification |
| --- | --- | --- |
| F9: base bus prints as today; coordination tables stay empty | 1 | `uv run pytest tests/local/test_base_compat.py -q` passes on `main` and on every PR |
| F9: legacy project keeps working without rejoin | 1 | `uv run pytest tests/coordination/test_legacy_compat.py -q` |
| F9/D17: v12 store migrates to v13, rows intact, new tables empty; fresh store at v13 | 2 | `uv run pytest tests/local/test_schema_delegation.py tests/coordination/test_availability.py tests/local/test_schema.py -q` |
| F9: only the D17 literal changes in existing tests | all | `TESTS_DIFF` as in Global constraints; `uv run poe check` passes |
| F1: delegate, bind, narrow, amend, widening needs rejoin (flags: SC1/SC2 checks in Tasks 7, 10, 11) | 2, 7, 10, 11 | `uv run pytest tests/coordination/test_delegation.py -q`; flag cases in the Task 7, 10, 11 test files below |
| F1/F2: approval verification matrix, claim guard, remote rejection | 3 | `uv run pytest tests/coordination/test_approvals.py -q` |
| F2: banner and delegation guidance | 4 | `uv run pytest tests/coordination/test_delegation_guidance.py -q` |
| F3: setup print and wizard | 5 | `uv run pytest tests/coordination/test_setup.py -q` |
| F8: docs match code | 6, 7, 9, 11, 13 | `uv run poe check`; reviewer inspection against `--help` output |
| F4/D20/D21: worker submit, coordinator accept sends record-bound approval, reject/supersede hides it, no self-certification | 7 | `uv run pytest tests/coordination/test_gate_records.py -q` |
| F5: objective amendment and guard | 8 | `uv run pytest tests/coordination/test_amendments.py -q` |
| F5/D19: instructions stored; verbatim only in full recovery output | 9 | `uv run pytest tests/coordination/test_instructions.py -q` |
| F6: decision records, provenance, acceptance | 10 | `uv run pytest tests/coordination/test_decisions.py -q` |
| F6/D22/D2: timed defaults, eligibility, digest; deadline wakes coordinator and blocked worker; lazy hook application; narrowed worker not accepted | 11 | `uv run pytest tests/coordination/test_decision_defaults.py -q` |
| F7: relay approval transport | 12 | `cd cloudflare && bun run check && bun run test` |
| F7: remote cap, negotiation, denied-peer bypass, coordinator-side delegation check at enqueue and upload, branch-not-target against the project set (R1), receiver 24 h cap, receiver-owner `amend` re-declares gates after `replace` (M2) | 13 | `uv run pytest tests/remote/test_remote_approvals.py -q` |

### Execution topology

| Wave | Tasks | Mode | Prerequisites | Isolation and integration | Wave verification |
| --- | --- | --- | --- | --- | --- |
| 1 | Task 1 | sequential-only | `bus-upgrade` merged | PR1 branch `feat/coordinator-delegation-gates` from `main` | `uv run pytest tests/local/test_base_compat.py tests/coordination/test_legacy_compat.py -q` passes before any source edit |
| 2 | Task 2 | sequential-only | Task 1 | PR1 branch | `uv run pytest tests/coordination tests/local -q`; `TESTS_DIFF` PR1 expectation |
| 3 | Task 3 | sequential-only | Task 2 `Delegation`, tables | PR1 branch | `uv run pytest tests/coordination tests/remote/test_coordination.py -q` |
| 4 | Task 4, Task 5 | parallel-safe | Task 3 | Separate worktrees off PR1 branch. Task 4 owns `cli/guidance.py` and its new test; Task 5 owns `coordination/setup.py`, `coordination/cli.py:add_parsers`, `cli/app.py` and its new test. Integrate Task 4 then Task 5; integrator of Task 5 resolves conflicts | `uv run poe check` on integrated branch |
| 5 | Task 6 | sequential-only | Tasks 2–5 | PR1 branch; open PR1 | `uv run poe check`; PR1 merged |
| 6 | Task 7 | sequential-only | PR1 merged | PR2 branch `feat/coordination-gate-records` | `uv run poe check`; PR2 merged |
| 7 | Task 8 | sequential-only | PR2 merged (shared `cli.py`, `delegation.py`) | PR3 branch `feat/coordination-amendments-instructions` | `uv run pytest tests/coordination -q` |
| 8 | Task 9 | sequential-only | Task 8 | PR3 branch | `uv run poe check`; PR3 merged |
| 9 | Task 10 | sequential-only | PR3 merged (uses amendments) | PR4 branch `feat/coordination-owner-decisions` | `uv run pytest tests/coordination -q` |
| 10 | Task 11 | sequential-only | Task 10 | PR4 branch | `uv run poe check`; PR4 merged |
| 11 | Task 12, Task 13 | parallel-safe | PR4 merged | PR5 branch `feat/coordination-remote-gates`; Task 12 worktree owns only `cloudflare/`; Task 13 worktree owns `src/`, `tests/remote/test_remote_approvals.py`, `tests/coordination/test_approvals.py`, docs. Integrate Task 12 then Task 13; Task 13 owner resolves | `uv run poe check` and `cd cloudflare && bun run check && bun run test` |

- Delivery status (revision 5): waves 1–5 (Tasks 1–6, PR1) are delivered on
  `feat/coordinator-delegation-gates` at `0fec966`; PR1 planner conformance
  `CONFORMS` (`conformance-pr1-a1.md`). Waves 6–11 remain. Accepted PR1
  deviations that later tasks consume: `effective(db, record, grant_id, *, now)`
  requires `now`; `BRANCH`/`branch_name` and `ticket_text` live in
  `models.py`; the gate branch checks the work branch against the project
  delegation's `all_targets()`, not the effective set; claims are deleted on
  leave, revoke and replace; `create_for(..., *, current)`; `coordinate --setup`
  routing shares the `collab` branch before `host_identity`; `show` adds only
  `delegation` and `bindings`.
- PR2 delivery status (revision 6, audit note; no semantic change): wave 6
  (Task 7) is delivered as PR #68, merged into `main` as `479f966`; PR2
  planner conformance `CONFORMS` (`conformance-pr2-a1.md`), PR review
  approved (`pr68-review-r2.md`). Waves 7–11 remain. Accepted PR2 deviations
  that later tasks consume:
  - The TTY setup wizard asks about auto-merge when a merge or merge-admin
    gate is kept (D26); `--setup` ignores `--auto-merge`.
  - `coordination show` `gate_records` rows use the key `record_id` (not
    `id`) and also carry `ticket`, `branch`, `base`, `review_evidence`,
    `required_checks` and `submitted_at` (PR review M1). These fields are
    untrusted worker-supplied references and appear in every command output
    that returns `store.show` (review N3). The query lives in `store.py`.
  - Dropping auto-merge blocks only new acceptances; `gate-reject` withdraws
    an accepted approval (review L2, documented).
  - The no-option amend error reads `amend needs --delegate, --no-delegation
    or a delegation flag` until Task 8 (R1 below).
  - `_declare_delegation(db, record, args, address, *, new)` takes `args`,
    not `specs`.
  - `ACTIONS` handlers take `(db, record, args, address)` and return
    `(dict | None, list[Message])`.
  - `Delegation.narrow` strips flag words and passes them as keywords to
    `from_specs`; it does not build directly.
  - Amend logic lives in `cli._amend`, called from `_transition`. Task 13's
    remote `amend` branch edits `_amend`, not an inline `_transition` block.
- PR2 carry-forward (implementer notes, no plan revision):
  - R1 (Task 8): when adding `--objective`, replace the `cli._amend` error
    text with `amend needs --objective or a delegation change` (SC2).
  - R2 (Tasks 10–11): `narrow` must also strip `owner-decisions` and
    `decision-defaults` and pass them as keywords to `from_specs`. Task 11
    widens `_flags` (currently `dict[str, bool]`) to carry a number.
- Parallel implementation permission: `not-requested`
- Host/model policy: detected host Claude Code. Planner ran as `claude-opus-5-5` under the controller's delegation (effort not exposed). Implementer/fixer and reviewer roles use the controller's resolved controls; none were supplied to this planner. No user overrides observed.
- Post-merge owner actions (not tasks; owner approval required; D25). No install, upgrade or copy happens after PR1–PR4; PR bodies list none. Once, after PR5 merges, on this machine only, in this order:
  1. Apply relay migration `0007` (`cloudflare/migrations/0007_coordination_approvals.sql`), then deploy the Worker. Migration before deploy, because the new Worker writes the `approval` column.
  2. From a clean `main` checkout at the PR5 merge commit, run `uv run llm-bus upgrade`. The first command of the new binary migrates the mailbox to schema version 13; older binaries then refuse that store (D16).
  3. Run `llm-bus install` to refresh the user-level Codex and Claude hooks.
  4. Copy `skills/llm-bus/` to `~/.agents/skills/llm-bus/` and `examples/instructions/llm-bus-coordination.md` to `~/.agents/instructions/llm-bus-coordination.md`, both from that `main` commit.
  - Until step 2, the installed tool on this machine stays at its pre-PR1 version and keeps using the default mailbox. Implementers and reviewers of PR2–PR5 run tests only (`uv run pytest`, `uv run poe check`; tests use temporary stores). Any manual `uv run llm-bus` command from a branch that opens a store must set `LLM_BUS_DB` to a scratch path, because a PR1-or-later binary would migrate the default mailbox and the installed binary would then refuse it. `--help` invocations exit before opening a store.
  - Other machines and remote peers are upgraded only by their own owners; this plan does not schedule them. A peer on an older client or relay leaves approvals queued (`unsupported_approvals`).

---

### Task 1: Compatibility baseline tests

**Goal:** Freeze current base-bus and legacy-coordination behavior as tests before any source change.

**Status:** delivered in PR1 (`20a720a`); not replanned.

**Delivers:** F9

**Execution:**

- Prerequisites: none.
- Verification timing: tests must pass on unmodified `main` before Task 2 starts, and unchanged in every later PR.
- Wave: 1
- Mode: sequential-only (gate for all later tasks).
- Isolation: PR1 branch.
- Integration order: first commit of PR1.

**Files:**

- Create: `tests/local/test_base_compat.py`
- Create: `tests/coordination/test_legacy_compat.py`

**Read first:**

- `tests/local/test_hook_guidance.py:test_hook_compact_guidance_and_recovery` — env-driven `main([...])` and hook invocation pattern.
- `tests/coordination/test_enrollment.py:invoke`, `create` — coordination CLI pattern.
- `src/llm_bus/mailbox/schema.py:ensure_schema` — current migration chain at `SCHEMA_VERSION = 12`; version 12 already creates the coordination tables in every store.

**Current-state evidence:**

- No test asserts exact `send`/`inbox`/`ack`/`lock` JSON or hook text with coordination never initialized.
- `coordination/store.py:show` returns project columns plus `references`, `grants`, `enrollments`, `targets`.
- `tests/coordination/test_availability.py:test_additive_schema_upgrade` asserts `user_version == 12` (the only literal version assertion; `tests/local/test_schema.py` uses `SCHEMA_VERSION`).

**Intended edits:**

- `tests/local/test_base_compat.py:test_base_bus_without_coordination` — with `LLM_BUS_DB`, missing `LLM_BUS_CONFIG` and two host identities, run `whoami`, `send` (stdin body), `inbox`, `ack`, `lock acquire NAME --check 30m --describe d`, `lock list`, `lock release NAME`, hook `SessionStart` and `UserPromptSubmit` for both kinds. Normalize only volatile values (timestamps, `tmp_path`) and assert exact JSON/text against literals captured from `main`. Assert every table whose name starts with `coordination_` has zero rows (enumerate from `sqlite_master`; no literal table set and no version literal, so the test stays valid after the version 13 migration).
- `tests/coordination/test_legacy_compat.py:test_project_without_delegation_unchanged` — create project, join worker, send each of the six operations; assert each inbox `coordination` dict has exactly keys `version, project_id, generation, grant_id, operation, verified, reason`; assert `coordination show` top-level key set equals the current literal set; second `join` reuses the grant (no rejoin required).

**Interfaces:**

- Consumes: `llm_bus.cli.app.main`, `llm_bus.coordination.cli.run`, `Store`.
- Produces: frozen expectations consumed by every later task gate.

**Dead-code scope:** no source change; no-code-impact. Confirm no duplicate of an existing test by grepping `tests/` for exact `send`/`inbox` JSON literals and `coordination show` key-set assertions.

**Review target:** expectations match `main` exactly; normalization does not hide output changes; no assertion depends on the schema version or table set; tests use only public CLI/store entrypoints.

- [ ] **Step 1: Establish the pre-change check**
  - Check: write both tests on PR1 branch at `main` head.
  - Cleanup: none.
- [ ] **Step 2: Prove the check characterizes current behavior**
  - Run: `uv run pytest tests/local/test_base_compat.py tests/coordination/test_legacy_compat.py -q`
  - Expect: all pass on unmodified source.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: none (tests only).
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: same as Step 2.
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`
  - Expect: pass.
  - Cleanup: no-code-impact.
- [ ] **Step 6: Review and record the task**
  - Review: independent review of literal expectations versus `main` behavior.
  - Record: commit `test: freeze base bus and legacy coordination behavior`.
  - Cleanup: none.

### Task 2: Schema version 13, delegation model, coordinate/amend and join binding

**Goal:** Add the version 13 migration for every table in this flow; store per-generation project delegation and per-grant worker bindings; expose them through `coordinate`, `coordination amend`, `join --narrow` and `coordination show`.

**Status:** delivered in PR1 (`610e5ae`); not replanned. Text below is corrected to the delivered signatures only.

**Delivers:** F1 (storage and owner commands), F9 (migration proof)

**Execution:**

- Prerequisites: Task 1 (regression net).
- Wave: 2
- Mode: sequential-only (Task 3 consumes `Delegation` and the tables).
- Isolation: PR1 branch.
- Integration order: after Task 1.

**Files:**

- Modify: `src/llm_bus/mailbox/schema.py:SCHEMA_VERSION`, `ensure_schema` (add `DELEGATION_SCHEMA_VERSION`)
- Modify: `src/llm_bus/coordination/store.py` (add `create_delegation_tables`), `show`
- Create: `src/llm_bus/coordination/delegation.py`
- Modify: `src/llm_bus/coordination/models.py` (add `GATE_OPERATIONS`)
- Modify: `src/llm_bus/coordination/cli.py:add_parsers`, `run`, `_transition`
- Modify: `tests/coordination/test_availability.py:test_additive_schema_upgrade` (the single literal `== 12` to `== 13`; no other edit)
- Test: `tests/local/test_schema_delegation.py`, `tests/coordination/test_delegation.py`

**Read first:**

- `src/llm_bus/mailbox/schema.py:ensure_schema` — `version < X` step chain inside `BEGIN IMMEDIATE`; `_version` refuses newer stores.
- `src/llm_bus/coordination/store.py:create_tables` — DDL style (`CREATE TABLE IF NOT EXISTS`, adjacent string fragments, `# ruff: noqa: ISC004`).
- `src/llm_bus/coordination/cli.py:run`, `_create`, `_transition` — owner action dispatch inside `BEGIN IMMEDIATE`.
- `src/llm_bus/coordination/store.py:join_session` — grant reuse/creation for both local and remote join.

**Current-state evidence:**

- `schema.py` sets `SCHEMA_VERSION = 12` and `COORDINATION_SCHEMA_VERSION = 12`; step `if version < COORDINATION_SCHEMA_VERSION: coordination_store.create_tables(db)` runs for every store, so empty coordination tables already exist in base-bus stores.
- `tests/coordination/test_availability.py:test_additive_schema_upgrade` drops three availability tables, sets versions 8–11, reopens and asserts `user_version == 12` and preserved grants.
- `cli.py:run` with action `coordinate` on an existing project resumes without reading any other flag.
- `store.py:join_session` reuses the latest unrevoked grant or creates a session-consent grant; both local and remote branches of `run` call it.
- `store.py:show` emits fixed keys only.

**Intended edits:**

- `schema.py` — `SCHEMA_VERSION = 13`; `DELEGATION_SCHEMA_VERSION = 13`; in `ensure_schema`, after the `PUBLICATION_SCHEMA_VERSION` step: `if version < DELEGATION_SCHEMA_VERSION: coordination_store.create_delegation_tables(db)`. No other step changes.
- `store.py:create_delegation_tables(db)` — `CREATE TABLE IF NOT EXISTS` for exactly these nine tables (all `TEXT NOT NULL` unless stated):
  - `coordination_delegations(scope, project_id, generation, delegation_json, updated_by, updated_at, PRIMARY KEY(scope,project_id,generation))`
  - `coordination_grant_delegations(grant_id TEXT PRIMARY KEY REFERENCES coordination_grants(grant_id), delegation_json, bound_at)`
  - `coordination_claims(address TEXT PRIMARY KEY, scope, project_id, ticket, branch, claimed_at)`
  - `coordination_approvals(message_id INTEGER PRIMARY KEY REFERENCES messages(id), approval_json)` (Task 3; Task 7 adds the optional `record_id` key inside `approval_json`, so no column is needed)
  - `coordination_gate_records(record_id TEXT PRIMARY KEY, scope, project_id, generation, grant_id REFERENCES coordination_grants(grant_id), ticket, branch, base, pr INTEGER NOT NULL, head_sha, verdict TEXT NOT NULL CHECK(verdict='approved'), evidence_json, checks_json, submitted_by, submitted_at, status TEXT NOT NULL CHECK(status IN ('submitted','accepted','rejected','superseded')), decided_by TEXT, decided_at TEXT, expires_at)` plus `CREATE INDEX IF NOT EXISTS coordination_gate_records_pr ON coordination_gate_records(scope,project_id,generation,pr)` (Task 7). `expires_at` is the acceptance deadline only (submission + 24 h); acceptance never rewrites it (D21).
  - `coordination_amendments(amendment_id TEXT PRIMARY KEY, scope, project_id, generation, text, sha256, decision_id TEXT, recorded_by, recorded_at)` (Task 8)
  - `coordination_instructions(instruction_id TEXT PRIMARY KEY, scope, project_id, audience TEXT NOT NULL CHECK(audience IN ('coordinator','worker')), kind TEXT NOT NULL CHECK(kind IN ('set','amend')), text, sha256, recorded_by, recorded_at)` (Task 9)
  - `coordination_owner_turns(address TEXT PRIMARY KEY, turn INTEGER NOT NULL, at)` (Task 10)
  - `coordination_decisions(decision_id TEXT PRIMARY KEY, scope, project_id, generation, class TEXT NOT NULL CHECK(class IN ('scope','security','cost','data','irreversible','objective')), ticket TEXT, worker TEXT, question, recommended TEXT, status TEXT NOT NULL CHECK(status IN ('pending','answered','defaulted')), answer TEXT, answer_sha256 TEXT, source_session TEXT, source_turn INTEGER, created_at, deadline_at TEXT, resolved_at TEXT)` (Tasks 10, 11). No `kind` column (D23/P3): class `objective` appends an amendment when answered, and only class `scope` can take a timed default.
- `models.py` — add `GATE_OPERATIONS: dict[str, str] = {"approve-commit": "commit", "approve-push": "push", "approve-merge": "merge", "approve-merge-admin": "merge-admin"}`. Do not change `OPERATIONS`.
- `delegation.py` — define:

```python
GATES = ("commit", "push", "merge", "merge-admin")
FLOOR = ("force-push", "deploys or production changes", "credential or secret changes",
         "paid or live runs", "merge with failing or pending CI")
MAX_TARGETS = 8  # branch validation is models.branch_name (delivered location)

@dataclass(frozen=True)
class Delegation:
    gates: tuple[tuple[str, tuple[str, ...]], ...]  # sorted by gate, targets sorted, non-empty
    def targets(self, gate: str) -> tuple[str, ...]: ...
    def all_targets(self) -> frozenset[str]: ...
    def intersect(self, other: "Delegation") -> "Delegation": ...
    def covers(self, other: "Delegation") -> bool: ...   # self is a superset of other
    def items(self) -> tuple[str, ...]: ...              # "GATE:BRANCH" strings
    def wire(self) -> dict[str, object]: ...             # {"version": 1, "gates": {...}}
    @classmethod
    def parse(cls, value: object) -> "Delegation": ...  # strict keys; later PRs add optional keys
    @classmethod
    def from_specs(cls, specs: list[str]) -> "Delegation": ...
    def narrow(self, items: list[str]) -> "Delegation": ...  # error if any item not in self.items()

def project_delegation(db, record: dict[str, object]) -> Delegation | None: ...  # None when no row
def set_project_delegation(db, record, delegation: Delegation | None, by: str) -> None: ...
def bind(db, record, grant_id: str, narrow: list[str] | None) -> Delegation | None: ...
def effective(db, record, grant_id: str, *, now: datetime) -> Delegation | None: ...  # project ∩ binding; None unless the grant is current at now (generation, unrevoked, unexpired, consent open, enrolled)
def rejoin_required(db, record) -> list[str]: ...  # enrolled worker addresses whose binding lacks project items; single owner of this computation (Task 4 consumes it)
```

  `from_specs` rejects unknown gates, invalid branch names, more than `MAX_TARGETS` per gate, and any spec whose text matches a `FLOOR` entry with a message naming the floor. No function checks whether a table exists.
- `cli.py:add_parsers` — `coordinate` gets `--delegate` (append); new action `amend` with `--delegate` (append) and `--no-delegation`; `join` gets `--narrow` (append). Leave `setup` unchanged (remote delegation is Task 13).
- `cli.py:run` — `coordinate` on a new project with `--delegate`: create, then `set_project_delegation`. On an existing project: if `--delegate` is given and differs from the stored delegation, raise `BusError("Delegation differs; use coordination amend PROJECT")`. `join` (both branches): after `store.join_session`, call `delegation.bind(db, record, grant_id, args["narrow"])` only when a project delegation exists; `--narrow` without project delegation raises `BusError`. A rejoin replaces the binding (accepts widening). `amend`: caller must equal `record["coordinator"]` and be enrolled as coordinator; replaces the current generation's delegation; result adds `rejoin_required`.
- `store.py:show` — add `delegation` (wire) and `bindings` (`{grant_id: items}`) keys only when rows exist for the project.
- `tests/coordination/test_availability.py` — change only `assert db.execute("PRAGMA user_version").fetchone()[0] == 12` to `== 13`.

**Interfaces:**

- Consumes: `store.project`, `store.join_session`, `store.enroll`.
- Produces: `SCHEMA_VERSION = 13`, `DELEGATION_SCHEMA_VERSION`, `store.create_delegation_tables`, the nine tables above (consumed by Tasks 3, 4, 7–11, 13), `GATE_OPERATIONS`, `delegation.Delegation`, `GATES`, `FLOOR`, `project_delegation`, `set_project_delegation`, `effective`, `bind`, `rejoin_required`; CLI `coordinate --delegate`, `coordination amend --delegate|--no-delegation`, `join --narrow`.

**Dead-code scope:** `schema.py`, `cli.py`, `store.py`, new module. Trace `ensure_schema` callers (`Store` connection setup) and `run` callers (`cli/app.py`) and `show` consumers (`run` results, tests). No removal candidates. Confirm `COORDINATION_SCHEMA_VERSION` stays used and no table-presence helper is introduced.

**Review target:** migration is additive (`CREATE TABLE IF NOT EXISTS` only, no `ALTER`, `DROP` or `UPDATE`), runs inside the existing transaction and preserves rows; every table and column matches the consuming tasks' interfaces (Tasks 3, 7–11), because later changes need a version 14; intersection and narrowing semantics (narrowing immediate, widening needs rejoin); no rows written for projects without delegation; floor unrepresentable; resume does not silently change delegation; amend restricted to current coordinator; the only existing-test edit is the D17 literal.

- [ ] **Step 1: Establish the pre-change check**
  - Check: `tests/local/test_schema_delegation.py`: (a) fresh store reports `user_version == SCHEMA_VERSION == 13` and all nine tables exist with zero rows after base-bus commands; (b) build a store with one project, one grant, one message and one lock, drop the nine tables, set `user_version=12`, reopen: version 13, the nine tables exist and are empty, project, grant, message and lock rows are byte-identical to before; (c) the same at `user_version=11` also lands at 13. `tests/coordination/test_delegation.py`: create with two gates; join binds both; `--narrow commit:develop` binds one; `--narrow merge:main` (not delegated) errors; amend to narrower set shrinks `effective` immediately; amend to wider set leaves `effective` unchanged and lists the worker in `rejoin_required`; rejoin accepts widening; `replace` leaves new generation with no delegation; resume with differing `--delegate` errors; floor-like spec rejected; project without `--delegate` writes no `coordination_delegations` row.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/local/test_schema_delegation.py tests/coordination/test_delegation.py -q`
  - Expect: fails (version is 12, tables missing, parser rejects `--delegate`, module missing).
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above, including the single test literal.
  - Cleanup: none expected.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/local -q`
  - Expect: pass, including Task 1 tests, `test_additive_schema_upgrade` at 13 and `tests/local/test_schema.py`.
  - Cleanup: confirm no unused imports.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF` and the PR1 `-U0` check from Global constraints.
  - Expect: check passes; only `tests/coordination/test_availability.py` listed, with one `- ... == 12` and one `+ ... == 13` line.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review of migration safety, table definitions against later interfaces, storage, CLI and lifecycle semantics.
  - Record: commit `feat(coordination): add schema v13 delegation storage and worker bindings`.
  - Cleanup: confirm no dead code in scope.

### Task 3: Approval directions, claims and gate authority

**Goal:** Add the four approval operations with structured approval data, worker claims, and receiver gate verification at send time and every exposure.

**Status:** delivered in PR1 (`8862fd0`, residuals `957a442`); not replanned. Delivered signature: `_gate_decision(db, record, *, coordinator, recipient, direction, approval, now)` (`authority.py:140-149`); the keyword list in "Intended edits" below is the pre-delivery draft.

**Delivers:** F1, F2 (structural guard)

**Execution:**

- Prerequisites: Task 2 (`Delegation`, `effective`, claims and approvals tables, `GATE_OPERATIONS`).
- Wave: 3
- Mode: sequential-only (touches `authority.py`, send path shared by Tasks 4–5 consumers).
- Isolation: PR1 branch.
- Integration order: after Task 2.

**Files:**

- Create: `src/llm_bus/coordination/approvals.py`
- Modify: `src/llm_bus/coordination/models.py:Direction.validate` (accept gate operations), add `Approval`
- Modify: `src/llm_bus/coordination/authority.py:authorize`
- Modify: `src/llm_bus/coordination/delivery.py:outgoing`, `persist`, `verification`
- Modify: `src/llm_bus/mailbox/writes.py:send_in_transaction`, `src/llm_bus/mailbox/store.py:Store.send`, `Store.send_in_transaction`
- Modify: `src/llm_bus/cli/commands.py:send`
- Modify: `src/llm_bus/coordination/cli.py:add_parsers`, `_transition` (claim)
- Modify: `src/llm_bus/cli/app.py:main` (route `coordination approve`)
- Test: `tests/coordination/test_approvals.py`

**Read first:**

- `src/llm_bus/coordination/authority.py:authorize` — denial precedence; grant `operations_json` check.
- `src/llm_bus/mailbox/writes.py:send_in_transaction` — already calls `authorize` and raises `Direction denied: REASON`.
- `src/llm_bus/coordination/delivery.py:verification`, `exposure_allowed` — exposure recheck.
- `src/llm_bus/remote/admission.py` — remote `authorize` caller (must deny gate ops in PR1).

**Current-state evidence:**

- `models.py:Direction.validate` rejects operations outside `OPERATIONS`.
- `authority.py:authorize` denies `operation_denied` when the operation is not in the grant's `operations_json`; grants store `json.dumps(OPERATIONS)`.
- `delivery.py:verification` returns `{**asdict(direction), "verified", "reason"}`.
- `cli/commands.py:send` routes `/` recipients to `enqueue_remote`.
- `cli/app.py:main` routes `coordinate`, `join` and `coordination` to `coordination.cli.run` after `host_identity`.

**Intended edits:**

- `models.py` — `Direction.validate`: accept `operation in OPERATIONS or operation in GATE_OPERATIONS`; fields unchanged. Add:

```python
@dataclass(frozen=True)
class Approval:
    ticket: str
    branch: str
    base: str
    expires_at: str
    pr: int | None = None
    head_sha: str | None = None
    def validate(self, operation: str) -> None: ...
    # approve-commit: no pr/head_sha; approve-push: head_sha required, no pr;
    # approve-merge(-admin): pr > 0 and head_sha required. head_sha matches [0-9a-f]{40}|[0-9a-f]{64}.
    # ticket: models.ticket_text. branch/base: models.branch_name. expires_at: RFC3339.
    def wire(self) -> dict[str, object]: ...  # omit None fields
    @classmethod
    def parse(cls, value: object, operation: str) -> "Approval": ...  # exact allowed key set
```

- `authority.py:authorize` — add keyword `approval: Approval | None = None`. Replace only the final operation check: if `direction.operation in GATE_OPERATIONS`, return `_gate_decision(db, scope=..., project_id=..., coordinator=sender, recipient=..., grant_id=..., operation=..., approval=..., now=...)`; otherwise keep the existing `operation_denied` check. `_gate_decision` denial order and reasons: approval missing or invalid → `invalid_direction`; coordinator contains `/` → `remote_gate_unsupported`; `effective` is `None` or lacks the gate → `gate_not_delegated`; `approval.base` not in gate targets → `target_denied`; `approval.branch == approval.base` or branch in the project delegation's `all_targets()` (delivered: the project set, not the effective set) → `target_denied`; expired → `expired`; claim missing or ticket/branch mismatch or claim project differs → `wrong_ticket`; else verified.
- `delivery.py` — `outgoing(..., approval: Approval | None = None)` validates approval against operation; `persist(db, message_id, direction, approval=None)` inserts into `coordination_approvals` when `approval` is given; `verification` loads the approval row only for gate operations (missing row → `None`, which `authorize` denies as `invalid_direction`), passes it to `authorize`, and adds `"approval": approval.wire()` to the result only for gate operations.
- `writes.py:send_in_transaction`, `Store.send`, `Store.send_in_transaction` — add keyword `approval: Approval | None = None`, pass to `authorize` and `persist`.
- `commands.send(store, args, sender, approval: Approval | None = None)` — if `approval` and recipient contains `/`: `BusError("Remote approval directions are not supported by this version")`.
- `approvals.py:approve(store, args, sender) -> object` — resolve recipient label; build `Approval` (`expires_at = now + parse_duration(args["expires_in"])`, default `2h`, maximum `24h`, else `BusError`); operation `"approve-" + gate`; fixed body `f"Approval {gate}: ticket {ticket}, branch {branch} into {base}" + (f", PR {pr}" if pr else "") + (f", head {head_sha}" if head_sha else "") + ". Verify live evidence before acting."`; call `commands.send(store, {"to": recipient, "body": body, "project": label, "direction": operation, "reply_to": None, "cross_folder": False, "format": args["format"]}, sender, approval=approval)`.
- `coordination/cli.py:add_parsers` — action `approve` (`--recipient` required, `--gate` choices `GATES`, `--ticket`, `--branch`, `--base` required, `--pr` int, `--head-sha`, `--expires-in` default `2h`); action `claim` (`--ticket`, `--branch` required). `_transition` `claim`: caller must be enrolled worker of this project; refuse with `BusError("Ticket or branch already claimed by ADDRESS")` when another address that is currently an enrolled worker of the same project and generation holds a claim with the same ticket or the same branch (D11; application check, no DDL change); otherwise upsert claim. Delivered addition: `leave`, `revoke` and `replace` delete the affected claims.
- `cli/app.py:main` — in the `coordination` branch, route `args["action"] == "approve"` to `approvals.approve(store, args, SenderContext(kind, session_id, project))`. This edit sits in the coordination branch, apart from bus-upgrade's `upgrade` dispatch.
- `remote/admission.py` — no edit: `authorize` without approval denies gate ops with `invalid_direction`; add a test proving it.

**Interfaces:**

- Consumes: Task 2 `effective`, `Delegation.all_targets`, `GATES`, tables `coordination_claims`, `coordination_approvals`.
- Produces: `Approval`, `authorize(..., approval=)`, `_gate_decision` (consumed by Task 7 through `authorize`), `persist(..., approval=)`, `outgoing(..., approval=)`, `Store.send(..., approval=)`, `approvals.approve`, CLI `coordination approve` and `coordination claim`; inbox `coordination.approval` key.

**Dead-code scope:** `authority.py`, `delivery.py`, `writes.py`, `store.py`, `commands.py`. Trace all `authorize` callers (`writes.py`, `delivery.py`, `store.py:join_session`, `cli/guidance.py:_worker_state`, `remote/admission.py`, `remote/outgoing.py`) and confirm default `approval=None` keeps their behavior. No removal candidates; `asdict` import in `delivery.py` stays used.

**Review target:** six-operation path unchanged; denial precedence; approval loaded and rechecked at exposure; atomic message+approval write; remote fail-closed on send and admission; claim cannot be written by non-enrolled sessions; no change to `send` CLI flags.

- [ ] **Step 1: Establish the pre-change check**
  - Check: `tests/coordination/test_approvals.py` matrix: verified approve-merge-admin with delegation, binding, claim; denials for each reason above; narrowing via amend makes a stored approval unverified at next inbox; revoke, leave, replace and grant expiry hide stored approvals; gate op without delegation rejected at send (`Direction denied: gate_not_delegated`); approval on remote recipient rejected; remote admission of a gate op without approval not admitted; a second enrolled worker claiming the same ticket or branch is refused, and succeeds after the first worker leaves; `send --direction` choices unchanged; inbox entry for `assign` has no `approval` key.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_approvals.py -q`
  - Expect: fails (unknown action `approve`).
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none expected.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/remote/test_coordination.py tests/local -q`
  - Expect: pass.
  - Cleanup: final reference check for `authorize(` call sites.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; PR1 expectation unchanged (only the D17 file).
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent security-focused review of authority changes and bypass attempts.
  - Record: commit `feat(coordination): verify delegated approval directions`.
  - Cleanup: confirm no dead code.

### Task 4: Session banner and delegation guidance

**Goal:** Hooks show enrolled sessions a banner and their delegation, floor and D6 rule text.

**Status:** delivered in PR1 (`d4033c0`, residuals `957a442`, wording `296f53b`); not replanned.

**Delivers:** F2, F1 (guidance), D6

**Execution:**

- Prerequisites: Task 3 (claims, `effective`, `FLOOR`).
- Wave: 4
- Mode: parallel-safe with Task 5 (disjoint files).
- Isolation: worktree `delegation-guidance` off PR1 branch; owns `src/llm_bus/cli/guidance.py`, `tests/coordination/test_delegation_guidance.py`.
- Integration order: first in wave 4.

**Files:**

- Modify: `src/llm_bus/cli/guidance.py:_coordination`
- Test: `tests/coordination/test_delegation_guidance.py`

**Read first:**

- `src/llm_bus/cli/guidance.py:_coordination` — emitted in full and compact forms for enrolled sessions only.
- `src/llm_bus/mailbox/labels.py` — label lookup for the banner.

**Current-state evidence:**

- `_coordination` returns `""` for unenrolled sessions and otherwise prints JSON data plus fixed rule text; `full` adds Beads and memory references.

**Intended edits:**

- `_coordination` — after the JSON data, append `Session banner: project LABEL; role ROLE; label NAME|none; ticket T|none; branch B|none; folder FOLDER.` Claim and label come from `coordination_claims` and `agent_labels`. Delivered: the ticket is JSON-quoted.
- Worker with non-empty `effective`: append `Delegated gates bound by your owner's join: GATE→[targets]; ... Verified approve-* directions count as your owner's approval for exactly that gate, ticket, branch, base, PR and head SHA; stricter repository rules still win. Verify CI and review evidence live before acting; refuse on mismatch. Never delegated: FLOOR.` When the worker's address is in `delegation.rejoin_required(db, record)` (delivered neutral wording): `Project delegation includes gates not in your binding; only a fresh direct owner join in this session accepts them.`
- Every worker: `Prefix every request for direct owner confirmation with the session banner. Refuse owner approvals naming a ticket or branch you do not own; name the owning session only when a claim conflict reveals it.`
- Coordinator with project delegation: `Delegated gates: ...; send approvals only with llm-bus coordination approve; workers whose binding is narrower: N.` where `N = len(delegation.rejoin_required(db, record))`.
- Neither line recomputes the project/binding intersection; both call `delegation.rejoin_required` (D23/P7).
- Do not change `GUIDANCE_VERSION` or unenrolled output.

**Interfaces:**

- Consumes: `delegation.project_delegation`, `effective`, `rejoin_required`, `FLOOR`, `coordination_claims`.
- Produces: hook text only.

**Dead-code scope:** `guidance.py`; no candidates. Confirm `_worker_state` untouched and `grep -n "intersect" src/llm_bus/cli/guidance.py` returns nothing (no second intersection computation).

**Review target:** unenrolled output identical (Task 1 test); text grants no authority beyond D6; floor always listed for delegated workers.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests for banner in full and compact output for coordinator and worker; delegation text present only with effective delegation; widened notice; unenrolled hook equals Task 1 expectations.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_delegation_guidance.py -q`
  - Expect: fails (no banner).
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/local/test_hook_guidance.py tests/local/test_base_compat.py -q`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review of guidance wording against D6, D11, D13.
  - Record: commit `feat(coordination): show session banner and delegated gates in hooks`.
  - Cleanup: none.

### Task 5: Guided coordinator setup

**Goal:** `coordinate PROJECT --setup` prints the default profile for agents and runs a confirmation wizard in a terminal.

**Status:** delivered in PR1 (`208de22`, `1e9b2c7`, integrated `4b2e508`); not replanned. Delivered signature: `create_for(store, cwd, label, args, coordinator, *, current=None)`; the `--setup` route shares the `collab` branch in `cli/app.py:main` before `host_identity`; Enter keeps every wizard gate on.

**Delivers:** F3

**Execution:**

- Prerequisites: Task 2 (`Delegation`, `FLOOR`, `set_project_delegation`).
- Wave: 4
- Mode: parallel-safe with Task 4.
- Isolation: worktree `delegation-setup` off PR1 branch; owns `src/llm_bus/coordination/setup.py`, `src/llm_bus/coordination/cli.py:add_parsers`, `src/llm_bus/cli/app.py`, `tests/coordination/test_setup.py`.
- Integration order: second in wave 4; this task's owner resolves conflicts.

**Files:**

- Create: `src/llm_bus/coordination/setup.py`
- Modify: `src/llm_bus/coordination/cli.py:add_parsers` (`--setup` on `coordinate`), add `create_for(store, cwd, label, args, coordinator, *, current=None) -> dict[str, object]`
- Modify: `src/llm_bus/cli/app.py:main` (route before `host_identity`)
- Test: `tests/coordination/test_setup.py`

**Read first:**

- `src/llm_bus/cli/app.py:_optional_address` — optional host identity.
- `src/llm_bus/coordination/cli.py:_create` — project creation without enrollment.
- bus-upgrade `PLAN.md` Task 1 `cli/app.py:main` edit — the `upgrade` branch sits before `Store(...)`; place the `--setup` branch after store creation and before `host_identity`, so the two do not interleave.

**Current-state evidence:**

- `app.py` requires host identity before any `coordinate` command.
- `_create` stores a project for any coordinator address; `coordinate` by that session later enrolls it (resume path).

**Intended edits:**

- `setup.py`:

```python
def detect_integration_branch(cwd: str) -> tuple[str | None, str]:
    # git -C cwd symbolic-ref --quiet --short refs/remotes/origin/HEAD -> strip "origin/";
    # returns (branch, "origin-head") or (None, "undetected"); no network.
def profile(cwd: str) -> dict[str, object]:
    # {"delegate": ["commit:B", "push:B", "merge:B", "merge-admin:B"] or [],
    #  "integration_branch": {...}, "floor": list(FLOOR),
    #  "steps": [...], "command": "llm-bus --format agent coordinate PROJECT --objective ... --beads-workspace ... --memory-ticket ... --delegate ..."}
    # steps tell the agent: check repository instructions for an integration branch
    # that overrides the detected one; ask the owner via the native question UI;
    # owner may disable items or change targets, never add floor items; run only confirmed flags.
def run_wizard(store, label: str, cwd: str, identity: str | None, *,
               ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> dict[str, object] | None:
    # asks objective, beads workspace, memory ticket, integration branch (default detected),
    # yes/no per gate, coordinator (default identity; else pick from store.agents(cwd));
    # prints review screen with every delegation, merge-admin bypass and floor, and the line
    # "Detected from origin/HEAD; check the repository instructions for a different integration branch.";
    # stores via cli.create_for only after answer "yes"; otherwise returns None.
```

- `app.py` — before `host_identity`: if `command == "coordinate" and args["setup"]`: if `sys.stdin.isatty() and sys.stdout.isatty() and args["format"] != "agent"` run `run_wizard(store, label, cwd, _optional_address())`; else print `profile(cwd)`. Return without registering a session.
- `cli.create_for` — one `BEGIN IMMEDIATE` transaction: `_create` then `set_project_delegation`; if coordinator is the current identity, enroll as coordinator; otherwise the result says that session must run `coordinate PROJECT`.

**Interfaces:**

- Consumes: `Delegation.from_specs`, `FLOOR`, `set_project_delegation`, `_create`.
- Produces: `setup.profile`, `setup.run_wizard`, `cli.create_for`, CLI flag `coordinate --setup`. Tasks 7 and 11 add profile items.

**Dead-code scope:** new module and `app.py` routing; confirm no duplicate branch detection elsewhere (`grep -rn symbolic-ref src`).

**Review target:** non-TTY path stores nothing and registers no session; wizard cannot add floor items or globs; confirmation required; detection has no network call; `upgrade` dispatch from bus-upgrade untouched.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests with a temp git repo whose `origin/HEAD` points to `develop`; non-TTY output JSON contains four `:develop` items, floor, steps; wizard review screen contains the repository-instructions branch hint; DB has no project and no new agent row; wizard with scripted `ask` answers creates project and delegation only after `yes`; `no` stores nothing; disabling `merge-admin` omits it.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_setup.py -q`
  - Expect: fails (`--setup` unknown).
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination -q` and `uv run llm-bus upgrade --help`
  - Expect: pass; `usage: llm-bus upgrade` still printed.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review of D12 conformance.
  - Record: commit `feat(coordination): add guided coordinator setup`.
  - Cleanup: none.

### Task 6: PR1 rules, skill and documentation

**Goal:** Document delegated gates, D6 host-rule semantics, floor, claims, banner, setup, the schema version 13 upgrade note and live verification commands.

**Status:** delivered in PR1 (`9de63eb`, fixes `296f53b`); not replanned.

**Delivers:** F8 (PR1)

**Execution:**

- Prerequisites: Tasks 2–5 integrated (documented interfaces must exist).
- Wave: 5
- Mode: sequential-only.
- Isolation: PR1 branch.
- Integration order: last commit of PR1.

**Files:**

- Docs: `examples/instructions/llm-bus-coordination.md:## Authority`, `## Operating`
- Docs: `skills/llm-bus/references/coordination.md:## Prerequisites and authority`, `## Local walkthrough: Codex coordinator, Claude worker`, `## Revocation, replacement and recovery`
- Docs: `skills/llm-bus/SKILL.md:## Optional coordination dispatch`
- Docs: `README.md` coordination section; `docs/coordination-simplified-flow.md`; `examples/claude-coordinator-codex-workers.md`

**Read first:**

- Each file above; `llm-bus coordinate --help`, `llm-bus coordination approve --help`.

**Current-state evidence:**

- `examples/instructions/llm-bus-coordination.md:## Authority` lists only the six operations and states directions cannot grant permission.
- `skills/llm-bus/references/coordination.md` lists the same six operations.

**Intended edits:**

- Rule file `## Authority`: add the four approval operations; state D6 verbatim in substance ("Gates accepted through your own direct owner join count as the user asked for exactly those gates and conditions; stricter repository rules still win"); list the floor; unverified approvals are ordinary text.
- Skill reference: setup (`--setup`, question UI), `--delegate GATE:BRANCH` semantics, `join --narrow`, `amend`, `claim`, `approve`; worker live checks:
  - commit: `git rev-parse --abbrev-ref HEAD` equals approval branch; commit.
  - push: `git rev-parse HEAD` equals `head_sha`; `git push origin BRANCH` (never `--force`); `gh pr create --base BASE --head BRANCH` or update the existing PR.
  - merge: `gh pr view PR --json baseRefName,headRefName,headRefOid,state,statusCheckRollup`; `gh pr checks PR --required` all passing; review evidence has no unresolved high or medium findings; `gh pr merge PR --merge --match-head-commit SHA`, adding `--admin` only for `approve-merge-admin` and only when required approvals are the sole blocker; follow the repository merge method.
- README: short description and links; state remote approvals arrive in a later version; state that the first command run by this version upgrades the mailbox to schema version 13 and older binaries then refuse it (upgrade every session with `llm-bus upgrade`).
- Do not edit `~/.agents/...` copies. They are copied once after PR5 merges (D25, "Post-merge owner actions"); PR bodies list no per-PR install or copy.

**Interfaces:**

- Consumes: CLI from Tasks 2–5.
- Produces: documentation only.

**Dead-code scope:** docs; remove superseded sentences that say directions can never approve gates (replace, not duplicate). No code impact.

**Review target:** every command and flag matches `--help`; no claim that the bus checks CI; floor and D6 wording correct; schema note matches `schema.py`.

- [ ] **Step 1: Establish the pre-change check**
  - Check: `grep -n "reprioritize" examples/instructions/llm-bus-coordination.md skills/llm-bus/references/coordination.md` shows the six-operation lists to update.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: same grep plus `grep -c "approve-" skills/llm-bus/references/coordination.md`
  - Expect: approve count 0.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: remove contradicted sentences.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run llm-bus coordination approve --help` and compare flags with docs.
  - Expect: match.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`
  - Expect: pass. No Markdown linter exists in the gate; doc accuracy is checked in Step 4 and review.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent document review.
  - Record: commit `docs(coordination): describe delegated approval gates`; open PR1 with `Closes` link if an issue is known.
  - Cleanup: none.

### Shared contracts for PR2–PR5 (revision 5)

Tasks 7, 8, 10, 11 and 13 reference these contracts instead of restating them.
Each task adds only its own field, flag or action. Evidence is the PR1 code at
`0fec966`.

**Current-state evidence (PR1):**

- `coordination/delegation.py:Delegation` has one field, `gates`. `_build`,
  `intersect`, `covers`, `items`, `wire`, `parse` (key set exactly
  `{"version", "gates"}`, non-empty `gates`), `from_specs` (at least one spec)
  and `narrow` (rebuilds through `from_specs`) handle gates only. Dataclass
  equality compares every field.
- `delegation.set_project_delegation` stores `binding.intersect(new)` per
  bound grant and deletes a binding whose `kept.gates` is empty.
  `delegation.rejoin_required` lists enrolled workers whose binding does not
  `covers(project)`. `delegation.effective(..., *, now)` returns
  `project.intersect(binding)`.
- `coordination/cli.py:add_parsers` gives `amend` a required mutually
  exclusive group (`--delegate` | `--no-delegation`); `_transition` `amend`
  replaces the whole delegation through `from_specs`.
- `coordination/cli.py:run` calls `_declare_delegation` on resume only when
  `--delegate` is given; it compares `from_specs(specs)` with the stored
  delegation.
- `coordination/cli.py:run` returns `store.show(...)` for every action (the
  `show` action returns early inside the transaction), adds `rejoin_required`
  for `amend`, and after commit wakes only `record["coordinator"]` for the
  availability notice id that `_transition` returns.
- `coordination/approvals.approve` is routed by `cli/app.py:main` around
  `run`; it calls `cli/commands.send`, which opens its own transaction in
  `Store.send`. `locks/notices.py:deliver(store, messages)` dispatches one wake
  per message recipient and reports a raised dispatch as `unknown`.

**SC1: delegation flags (R2).** Each later task adds one optional field to
`Delegation`, after `gates`: `auto_merge: bool = False` (Task 7),
`owner_decisions: bool = False` (Task 10), `decision_default_minutes: int |
None = None` (Task 11). For every field:

- `_build(mapping, *, auto_merge=..., ...)` takes it as a keyword.
- `wire` emits the key only when set (`auto_merge: true`,
  `owner_decisions: true`, `decision_default_minutes: N`); `parse` accepts
  exactly `{"version", "gates"}` plus the optional keys known to this binary,
  with strict types (bool, or int 1–1440).
- `items` appends the flag item after the gate items: `auto-merge`,
  `owner-decisions`, `decision-defaults`. `show` `bindings` and `join` output
  therefore list flags without a new key.
- `intersect`: a flag is set only when both sides set it;
  `decision_default_minutes` is `self`'s value when both sides have one,
  else `None`. `effective` calls `project.intersect(binding)`, so the project
  owner's minutes apply.
- `covers(other)`: gate subset as today, and every flag set in `other` is set
  in `self` (for minutes: presence only). `rejoin_required` therefore reports
  a worker when the project adds a flag after the worker joined. A minutes
  value change is not widening: the worker's item is `decision-defaults`, and
  the timing belongs to the coordinator owner (D9).
- `from_specs(specs, *, auto_merge=False, ...)` takes the flags as keywords
  and validates dependencies on input only: `auto_merge` needs a `merge` or
  `merge-admin` gate; `decision_default_minutes` needs `owner_decisions`.
  `parse` does not check dependencies, because an intersection may keep a
  flag whose gate targets no longer overlap; consumers check the gate itself.
- `narrow(items)` keeps exactly the listed items: gate items as today plus
  flag items, and fails on any item not in `self.items()`. It applies the same
  dependency rule as `from_specs`; it no longer rebuilds through
  `from_specs(items)`, which rejects flag words.
- Non-empty rule (D24, C1 option B): a stored delegation, a binding and the
  result of `from_specs` or `narrow` are valid when `items()` is non-empty
  (at least one gate or flag). PR1 assumes a gate in four places (`parse`,
  `from_specs`, the binding delete in `set_project_delegation`, and
  `guidance._delegation_text`, whose coordinator gate line prints whenever a
  project delegation exists and whose worker gate line is guarded by
  `granted.gates`); Task 10 changes all four, because `owner_decisions` is the only flag valid without a gate.
  Before Task 10, the input dependency rules above still reject every
  gate-less `from_specs` call (`auto_merge` needs a merge gate;
  `decision_default_minutes` needs `owner_decisions`). The fixed floor is
  unchanged.

**SC2: `coordinate`, `amend` and resume flags (R3).** Every delegation flag a
task adds goes on `coordinate` and `amend`, never on `join` (workers narrow
with `join --narrow ITEM`).

- `amend` parser: the `--delegate` | `--no-delegation` group stays mutually
  exclusive but becomes optional; flags join the `--delegate` side;
  `--objective` (Task 8) is independent. `_transition` `amend` raises
  `BusError("amend needs --objective or a delegation change")` when none is
  given, and `BusError("--no-delegation takes no delegation flags")` when
  `--no-delegation` comes with a flag.
- Replace semantics: a delegation change restates the whole delegation.
  `amend --delegate ... [--auto-merge] [--owner-decisions]
  [--decision-defaults N]` builds `from_specs(delegate, flags...)` and
  replaces the stored one; an omitted flag is removed. A flag without
  `--delegate` follows the same rule: it builds `from_specs([], flags...)`,
  which replaces the stored delegation with a gate-less one. Before Task 10
  `from_specs` rejects it (dependency or non-empty error); from Task 10,
  `amend --owner-decisions` alone stores a decisions-only delegation and
  removes every gate (narrowing, applied at once per D14). There is no
  separate "restate the gates" error. `amend --objective` alone leaves the
  delegation untouched.
- Resume: `run` calls `_declare_delegation` when `--delegate` or any
  delegation flag is given. It compares `from_specs(delegate, flags...)`
  with the stored delegation by dataclass equality, so a differing flag
  raises `Delegation differs; use coordination amend PROJECT`. No flag and no
  `--delegate` keeps today's behavior (no comparison).
- `cli.create_for` and `setup.run_wizard` pass the same flags to
  `from_specs`, so setup and `coordinate` store identical delegations.
  `create_for` stores a delegation when `--delegate` or any flag is given
  (PR1 checks `if specs:` only).
- Gate specs and flags stay apart from the first flag (Task 7, L1):
  `run_wizard` (PR1: `"delegate": list(chosen.items())`) puts only gate specs
  (`GATE:BRANCH`) under `delegate` and each chosen flag under its own args key
  (`auto_merge`, later `owner_decisions`, `decision_default_minutes`), so no
  flag word such as `auto-merge` reaches `from_specs`. `setup.profile` lists
  flag items under a new `flags` key (never inside `delegate`), and its
  `command` shows them as their own
  options (`--auto-merge`, later `--owner-decisions`, `--decision-defaults 30`).

**SC3: action results and wakes (R5).** New coordination actions (Task 7
`gate-*`, Task 9 `instructions`, Task 10 `decision-*`) are dispatched from
`run` to functions that run inside `run`'s `BEGIN IMMEDIATE` and return
`tuple[dict[str, object] | None, list[Message]]` (`instructions` returns
`(None, [])`). In `run`:

- When the result is not `None`, `run` returns it instead of
  `store.show(...)`. When it is `None`, `run` returns `store.show(...)` as
  today.
- After commit, `run` calls `locks.notices.deliver(mailbox, messages)` and
  adds `wakes` (the returned `Delivery` list) to the result only when
  `messages` is non-empty. Each message's own recipient is woken. Existing
  actions keep the `availability_wake` path unchanged.
- No new action calls `dispatch_pending` directly.

**SC4: lazy decision defaults in the CLI (R4).** Only actions that run
inside `run` apply due defaults lazily (Task 11): `show`, `amend`,
`gate-record`, `gate-accept`, `gate-reject`, `decision-show` and
`decision-list` (revision 4's list without `approve`). `run` calls
`decisions.apply_due(db, record)` right after `store.project(...)`, inside the
same transaction, and adds the returned messages to the SC3 delivery list.
The `show` early return becomes an assignment so delivery runs after commit.
`coordination approve` is not a lazy caller: it runs through
`approvals.approve`, `commands.send` and `Store.send` in its own transaction,
outside `run`. The coordinator's own hook applies due defaults on each
coordinator prompt before the agent runs `approve`, so `approve` needs no
separate pre-send transaction.

### Task 7: Merge gate record, coordinator acceptance and auto-merge (PR2)

**Goal:** The worker submits merge gate records; the coordinator accepts them, which sends the worker an ordinary record-bound merge approval, or rejects them; the bus hides that approval once the record leaves `accepted`.

**Delivers:** F4, F3 (profile item), F8 (PR2 docs)

**Execution:**

- Prerequisites: PR1 merged (`_gate_decision` through `authorize`, `Approval`, claims, `Delegation`, `coordination_gate_records` table).
- Wave: 6
- Mode: sequential-only.
- Isolation: PR2 branch from `main`.
- Integration order: only task in PR2.

**Files:**

- Create: `src/llm_bus/coordination/gate_records.py`
- Modify: `src/llm_bus/coordination/delegation.py:Delegation` (field `auto_merge`, item `auto-merge`, per SC1 in `_build`, `intersect`, `covers`, `items`, `wire`, `parse`, `from_specs`, `narrow`)
- Modify: `src/llm_bus/coordination/models.py:Approval` (optional `record_id: str | None = None`)
- Modify: `src/llm_bus/coordination/authority.py:_gate_decision` (record-status check)
- Modify: `src/llm_bus/coordination/cli.py:add_parsers`, `run` (SC3 action results and wakes; SC2 resume flag), `_transition` (`amend` per SC2), `_declare_delegation`, `create_for`; `src/llm_bus/coordination/store.py:show`; `src/llm_bus/coordination/setup.py:profile`, `run_wizard`
- Test: `tests/coordination/test_gate_records.py`
- Docs: `skills/llm-bus/references/coordination.md`, `examples/instructions/llm-bus-coordination.md`

**Read first:**

- `src/llm_bus/coordination/authority.py:_gate_decision` (from Task 3).
- `src/llm_bus/coordination/models.py:Approval`, `src/llm_bus/coordination/delivery.py:outgoing`, `persist`, `verification` (Task 3) — the approval path the accept step reuses.
- `src/llm_bus/coordination/delegation.py:effective`, `Delegation` — "Shared contracts for PR2–PR5" SC1–SC3 above.
- `src/llm_bus/coordination/store.py:create_delegation_tables` — `coordination_gate_records` columns and status values (Task 2).
- `src/llm_bus/mailbox/writes.py:send_in_transaction`; `src/llm_bus/locks/notices.py:deliver` — plain notice, then wake after commit (SC3).

**Current-state evidence:**

- After PR1, merges require a verified `approve-merge(-admin)` direction from the coordinator; `coordination_gate_records` exists and is empty.
- After PR1, `writes.send_in_transaction(..., direction=, approval=)` runs `authorize` with the approval and persists message, direction and approval in the caller's transaction; `Approval.parse` accepts an exact key set.

**Intended edits:**

- `gate_records.submit(db, record, caller, *, ticket, branch, base, pr, head_sha, evidence: list[str], checks: list[str], now) -> tuple[dict[str, object], list[Message]]` (SC3):
  - Caller must be an enrolled worker of the current generation with a current grant; a caller equal to `record["coordinator"]` or enrolled as coordinator raises `BusError("Only the worker that claims the branch submits gate records")`.
  - Claim must match `ticket` and `branch`; `effective(db, record, grant_id, now=now)` must have `auto_merge` and a `merge` or `merge-admin` gate whose targets include `base`; otherwise `BusError` naming `wrong_ticket` or `gate_not_delegated`.
  - Validates fields through `Approval(ticket, branch, base, now + 24h, pr, head_sha).validate("approve-merge")`; 1–8 evidence references (each 1–512 chars, no control characters); 1–32 check names.
  - Marks earlier rows with status `submitted` or `accepted` for the same (scope, project_id, generation, pr, branch) as `superseded` (L3: `branch` equals the submitter's own claim, and claim uniqueness stops another worker from holding it, so a second worker cannot withdraw this worker's approval by naming its PR); inserts a new row with `record_id=uuid4().hex`, `status='submitted'`, `grant_id`, `submitted_by=caller`, `submitted_at=now`, `expires_at=now + 24h`, `verdict='approved'`.
  - Sends the coordinator a plain message through `send_in_transaction(db, caller, coordinator, body, cross_folder=True)`, where `body = f"Gate record {record_id} submitted for PR {pr} head {head_sha}. Verify live, then run coordination gate-accept."` The text grants nothing. Returns `({"record_id", "status": "submitted", "expires_at", "superseded": [ids]}, [notice])`; `run` wakes the coordinator after commit (SC3).
- `gate_records.accept(db, record, caller, *, record_id, head_sha, admin: bool, now) -> tuple[dict[str, object], list[Message]]` (SC3) — runs inside `run`'s `BEGIN IMMEDIATE`. Record checks raise `BusError(f"Gate record not accepted: {reason}")` in this order: caller is not `record["coordinator"]` or not enrolled as coordinator in the current generation → `not_coordinator`; row missing or for another project → `no_record`; row generation differs from current → `stale_generation`; status is not `submitted` → `not_submitted`; `now >= row.expires_at` → `expired`; `head_sha != row.head_sha` → `sha_mismatch`; project delegation or `effective(db, record, row.grant_id, now=now)` lacks `auto_merge` → `gate_not_delegated`. Then:
  - set `status='accepted'`, `decided_by=caller`, `decided_at=now` (`expires_at` unchanged);
  - `operation = "approve-merge-admin" if admin else "approve-merge"`;
  - `approval = Approval(row.ticket, row.branch, row.base, now + 24h, row.pr, row.head_sha, record_id=row.record_id)`;
  - `direction = delivery.outgoing(db, sender=caller, scope=..., label=..., recipient=row.submitted_by, operation=operation, approval=approval)`;
  - `message = send_in_transaction(db, caller, row.submitted_by, body, cross_folder=True, direction=direction, approval=approval)` with `body = f"Approval {gate}: gate record {record_id}, ticket {ticket}, branch {branch} into {base}, PR {pr}, head {head_sha}. Wait for required CI on that SHA, verify live, then merge."`. This is the same `authorize` gate branch as `coordination approve`, so a changed claim, revoked grant, missing covering gate or wrong target raises `BusError("Direction denied: REASON")` unchanged and the whole transaction rolls back (record stays `submitted`).
  - return `({"record_id", "status": "accepted", "message_id", "operation", "expires_at": approval.expires_at}, [message])`; `run` returns this result, not `show`, and wakes the submitter (`message["recipient"]`) after commit (SC3).
- `models.py:Approval` — add `record_id: str | None = None` as the last field. `validate`: allowed only for `approve-merge` and `approve-merge-admin`, format `[0-9a-f]{32}` (the `uuid4().hex` record id `submit` writes). `wire` omits it when `None`; `parse` accepts it as an optional key. `coordination approve` has no flag for it, so only `gate_records.accept` creates record-bound approvals.
- `authority.py:_gate_decision` — after the `remote_gate_unsupported` check and before the delegation check: if `approval.record_id` is set, load the row by `record_id`; deny `record_withdrawn` when the row is missing, its scope, project or generation differs from the direction, its status is not `accepted`, or any of ticket, branch, base, pr, head_sha differs from the approval. Approvals without `record_id` are unaffected. Because `delivery.verification` reauthorizes at every exposure, `reject` and a superseding `submit` hide an already sent approval.
- `gate_records.reject(db, record, caller, *, record_id, now) -> tuple[dict[str, object], list[Message]]` (SC3) — current coordinator only; status `submitted` or `accepted` becomes `rejected` with `decided_by` and `decided_at`; plain notice from the coordinator to the submitter (`f"Gate record {record_id} was rejected; do not merge on its approval."`); returns `({"record_id", "status": "rejected"}, [notice])`, and `run` wakes the submitter after commit; other statuses raise `not_submitted`.
- CLI: `coordinate`/`amend` `--auto-merge` (SC1 dependency: a merge or merge-admin gate; SC2 replace and resume semantics); `join --narrow auto-merge`; actions `gate-record` (`--ticket --branch --base --pr --head-sha --review-evidence (append) --required-check (append)`; no `--verdict` flag, `submit` writes `verdict='approved'`), `gate-accept` (`--record-id --head-sha --admin`) and `gate-reject` (`--record-id`).
- `store.py:show` — add `gate_records` (current-generation rows: id, pr, head_sha, status, submitted_by, decided_by, expires_at) only when rows exist.
- Setup profile adds `auto-merge` under `flags` and `--auto-merge` in `command`; `run_wizard` asks for it and passes `auto_merge` per SC2 (gate specs only under `delegate`); its review screen states that each merge still needs coordinator acceptance, which sends the merge approval.
- Docs: worker flow: after the review loop passes, submit `gate-record`; when a verified `approve-merge(-admin)` approval with `record_id` arrives, wait for required CI on that SHA, run the Task 6 merge live checks and merge with `--match-head-commit`; on any new push, submit again (the old approval becomes unverified); if the approval expires before CI passes, submit again. Coordinator flow before `gate-accept`: `gh pr view PR --json baseRefName,headRefName,headRefOid,state` matches the record; open each review evidence reference and confirm it covers that head SHA with no unresolved high or medium findings; required check names match the repository's required checks; pass `--admin` only when required approvals are the sole blocker. Acceptance does not replace the worker's live CI check.

**Interfaces:**

- Consumes: `authorize`, `Approval`, `delivery.outgoing`, `effective(..., now=)`, claims, `coordination_gate_records`, `coordination_approvals`, `send_in_transaction(..., direction=, approval=)`, `locks.notices.deliver`.
- Produces: `gate_records.submit`, `accept`, `reject`, `Approval.record_id`, denial reason `record_withdrawn`, `Delegation.auto_merge`, CLI `gate-record`, `gate-accept`, `gate-reject`, `--auto-merge`; the SC1 flag mechanics, SC2 `amend`/resume flag handling and SC3 action-result dispatch in `run` (reused by Tasks 8–11); `show` key `gate_records`; inbox `coordination.approval.record_id` for record-bound approvals.

**Dead-code scope:** new module; `delegation.py` parse/wire extension; `Approval` and `_gate_decision` extensions. No removals in source. Confirm no readiness function, no `gate-ready` action and no second post-acceptance expiry exist (`grep -rn "gate-ready\|readiness\|auto_merge_not_bound" src tests` returns nothing); no second SHA validator beyond `Approval.validate`; accept sends through `send_in_transaction`, not a copy of `approvals.approve`.

**Review target:** no self-certification path (coordinator cannot submit; worker cannot accept; `coordination approve` cannot set `record_id`); acceptance verification order and reasons; record status change and approval write are atomic; SHA and generation binding; supersede and reject hide the sent approval at send and exposure; one acceptance-deadline expiry plus the approval's own expiry; notices are plain text and grant nothing; acceptance cannot pass without both owners binding auto-merge.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests for: accept path (submit, accept) yields a verified `approve-merge` approval with `record_id` in the worker's inbox, and `--admin` yields `approve-merge-admin`; coordinator `gate-record` rejected; worker `gate-accept` rejected (`not_coordinator`); accept with wrong SHA (`sha_mismatch`); accept after 24 h (`expired`); accept after `replace` (`stale_generation`); accept twice (`not_submitted`); accept after delegation amended to drop `auto_merge`, and after the worker rejoined with `--narrow` dropping `auto-merge` (`gate_not_delegated`); accept after the worker re-claims another branch raises `Direction denied: wrong_ticket` and leaves the row `submitted` with no new message; `--admin` without a `merge-admin` gate raises `Direction denied: gate_not_delegated`; `gate-reject` after accept makes the stored approval `verified: false` with `record_withdrawn` and hides it from inbox; resubmission for the same PR and branch supersedes the accepted record with the same effect; a second worker's record naming the first worker's PR (its own claimed branch) leaves the first worker's approval verified (L3); a wizard run with auto-merge enabled stores `auto_merge` and passes no flag word to `from_specs`, and `coordinate --setup` lists `auto-merge` under `flags`, not `delegate` (L1); a worker without `auto-merge` bound cannot submit; a hand-built approval row with an unknown `record_id` is `record_withdrawn`; approvals without `record_id` are unaffected; `coordination approve --help` has no record flag; submit and reject notices stored; legacy project unaffected. SC1–SC3 checks: `gate-record` wakes the coordinator, and `gate-accept` and `gate-reject` wake the submitter (recorder on `llm_bus.locks.notices.dispatch_pending`); `gate-accept` returns its own result keys, not `show`; `amend` adding `--auto-merge` lists a joined worker in `rejoin_required` and leaves its `effective` without `auto_merge` until it rejoins; `amend --delegate merge:develop` without `--auto-merge` removes it; `--auto-merge` without a merge gate is rejected; resume with `--auto-merge` differing from the stored delegation raises `Delegation differs`; `join --narrow merge:develop --narrow auto-merge` binds both; `coordination show` `bindings` lists `auto-merge`; a PR1-shaped stored delegation (no flag keys) still parses.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_gate_records.py -q`
  - Expect: fails (unknown action `gate-record`).
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination -q`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; empty.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent security review of the trust split.
  - Record: commit `feat(coordination): add merge gate records with record-bound approvals`; open PR2.
  - Cleanup: none.

### Task 8: Objective amendments and coordinate guard (PR3)

**Goal:** Append dated objective amendments and reject a differing `--objective` on resume.

**Delivers:** F5 (amendments)

**Execution:**

- Prerequisites: PR2 merged (shared `cli.py`).
- Wave: 7
- Mode: sequential-only.
- Isolation: PR3 branch.
- Integration order: first in PR3.

**Files:**

- Create: `src/llm_bus/coordination/amendments.py`
- Modify: `src/llm_bus/coordination/cli.py:add_parsers` (`amend --objective`, independent of the SC2 delegation group), `run` (resume guard), `_transition` (`amend --objective`)
- Modify: `src/llm_bus/coordination/store.py:show`
- Test: `tests/coordination/test_amendments.py`

**Read first:**

- `src/llm_bus/coordination/cli.py:run` — resume path ignores `--objective`.
- `src/llm_bus/coordination/store.py:create_delegation_tables` — `coordination_amendments` columns (Task 2).

**Current-state evidence:**

- `run` with `coordinate` on an existing project never compares `args["objective"]` with the stored `objective`.
- `amend` requires `--delegate` or `--no-delegation` at parse time (PR1); Task 7 made that group optional (SC2), so `amend --objective TEXT` parses.

**Intended edits:**

- `amendments.append(db, record, text: str, by: str, decision_id: str | None = None) -> dict[str, object]` writes `coordination_amendments`; `amendments.effective_objective(db, record) -> str` (latest amendment text, else stored objective); `amendments.history(db, record) -> list[dict[str, object]]`.
- `run` resume: if `args["objective"]` is given and differs from `effective_objective`, raise `BusError("Objective differs from the stored objective; use coordination amend PROJECT --objective")`.
- `amend --objective TEXT` (current coordinator only) appends. Without a delegation change it leaves the delegation untouched; with one, the SC2 replace applies in the same transaction.
- `show` adds `amendments` only when rows exist.

**Interfaces:**

- Produces: `amendments.append`, `effective_objective`, `history` (consumed by Task 10).

**Dead-code scope:** `cli.py` resume path; no removals.

**Review target:** the only intended behavior change to an existing command (D8); same objective still resumes; history append-only.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests: same objective resumes; differing objective errors and stores nothing; amend appends history and changes effective objective; `amend --objective` alone keeps the stored delegation and bindings unchanged; `amend` with neither objective nor delegation change errors; non-coordinator amend rejected.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_amendments.py -q`
  - Expect: fails.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination -q`
  - Expect: pass (existing tests resume with the same objective).
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; empty.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review.
  - Record: commit `feat(coordination): record objective amendments`.
  - Cleanup: none.

### Task 9: Standing owner instructions (PR3)

**Goal:** Store owner standing instructions and inject them verbatim only in the full recovery form of the coordination hook block.

**Delivers:** F5 (instructions), F8 (PR3 docs)

**Execution:**

- Prerequisites: Task 8.
- Wave: 8
- Mode: sequential-only.
- Isolation: PR3 branch.
- Integration order: second in PR3.

**Files:**

- Create: `src/llm_bus/coordination/instructions.py`
- Modify: `src/llm_bus/coordination/cli.py:add_parsers`, `run`, `_transition`
- Modify: `src/llm_bus/cli/guidance.py:_coordination`
- Modify: `src/llm_bus/coordination/store.py:show`
- Modify: `src/llm_bus/coordination/setup.py:profile`, `run_wizard` (optional follow-up step)
- Test: `tests/coordination/test_instructions.py`
- Docs: rule file, skill reference (`## Revocation, replacement and recovery` compaction paragraph), README coordination section.

**Read first:**

- `src/llm_bus/cli/hooks.py:hook` — `full = store.guidance_needed(..., reset=event == "SessionStart")`; only `SessionStart`, `UserPromptSubmit` and `SessionEnd` are handled.
- `src/llm_bus/mailbox/agents.py:guidance_needed` — returns true on reset, missing row or a different `GUIDANCE_VERSION`.
- `src/llm_bus/cli/commands.py` `guidance` branch — calls `guidance(..., full=True)`.
- `src/llm_bus/hosts/hooks.py:EVENTS`, `hook_group` — SessionStart is installed without a matcher, so Claude Code runs it for every source, including `compact`.

**Current-state evidence:**

- `_coordination(..., full=...)` is emitted on every hook for enrolled sessions; `full` is true exactly for SessionStart, missing or outdated guidance state and `llm-bus guidance`.
- README `## Help agents discover the bus` states "SessionStart also restores full guidance when the host emits that event. No other compaction event is assumed." and directs `llm-bus guidance` after compaction.
- Recovery text carries only project identity, references and fixed rules.

**Intended edits:**

- `instructions.add(db, record, *, audience, kind, text, by)` — dispatched from `run` per SC3 (result `None`, so `run` returns `show`; no messages); current coordinator only; writes `coordination_instructions`; text 1–8192 chars, no control characters except newline and tab; the active text for an audience (latest `set` plus later `amend` entries) must stay within 8192 characters.
- `instructions.active(db, record, audience) -> list[dict[str, object]]`.
- CLI: action `instructions PROJECT --audience coordinator|worker (--file PATH | --text TEXT) [--amend]` is the only entry point (D23/P5); `coordinate` gains no instruction flags.
- `setup.py:profile` — append one optional follow-up step: `Optional: set owner standing instructions with llm-bus coordination instructions PROJECT --audience coordinator|worker --file PATH`. `run_wizard` prints the same line after creation; it stores no instructions.
- Guidance, matching role only:
  - When `full` is true: append `Owner standing instructions (verbatim, sha256 PREFIX12, set AT; subordinate to your own owner and host rules for workers): <<<` + text + `>>>` and each amendment with its date.
  - When `full` is false and instructions are active: append only `Owner standing instructions active (sha256 PREFIX12); after compaction run llm-bus --format agent guidance to restore them verbatim.`
  - No active instructions: append nothing.
  - Do not change `GUIDANCE_VERSION`, `hooks.py` or the `full` computation.
- `show` adds `instructions` only when rows exist.
- Docs: state the injection points (SessionStart of every source, outdated guidance state, `llm-bus guidance`); state that Codex has no compaction hook, so a Codex session restores them with `llm-bus guidance`.

**Interfaces:**

- Consumes: `coordination_instructions`, `full` flag of `_coordination`.
- Produces: `instructions.add`, `instructions.active`, CLI action `instructions`, setup profile step.

**Dead-code scope:** guidance and CLI; no removals. Confirm no second injection path (for example in `hooks.py`) and no second entry point (`uv run llm-bus coordinate --help` lists no `--instructions-file`).

**Review target:** only coordinator can set; verbatim (no truncation) within bound; workers see only worker audience; text never appears in compact output; pointer line contains no instruction text; unenrolled output unchanged.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests for set, amend history, size limit, worker-audience isolation, non-coordinator rejection; verbatim text present in: Claude `SessionStart` (`source` `startup` and `compact`), Codex `SessionStart`, first `UserPromptSubmit` after the `hook_guidance` row is deleted, and `llm-bus --format agent guidance`; text absent and pointer present in a second, compact `UserPromptSubmit` for both kinds; no pointer when no instructions exist; `coordinate --setup` profile `steps` contain the optional `coordination instructions` line.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_instructions.py -q`
  - Expect: fails.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/local -q`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; empty.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review against D10 and D19.
  - Record: commit `feat(coordination): add standing owner instructions`; open PR3.
  - Cleanup: none.

### Task 10: Owner decision records (PR4)

**Goal:** Record owner decisions with provenance and let bound workers accept them.

**Delivers:** F6 (records)

**Execution:**

- Prerequisites: PR3 merged (`amendments.append`).
- Wave: 9
- Mode: sequential-only.
- Isolation: PR4 branch.
- Integration order: first in PR4.

**Files:**

- Create: `src/llm_bus/coordination/decisions.py`
- Modify: `src/llm_bus/coordination/delegation.py:Delegation` (field `owner_decisions`, item `owner-decisions`, per SC1); `parse`, `from_specs` (D24 non-empty rule); `src/llm_bus/coordination/delegation.py:set_project_delegation` (binding delete)
- Modify: `src/llm_bus/coordination/cli.py:add_parsers`, `run` (SC3 dispatch of `decision-*`), `_transition` (`amend` flag per SC2), `_declare_delegation`, `create_for` (store when `--delegate` or a flag is given); `src/llm_bus/coordination/setup.py:run_wizard` (pass the flag per SC2)
- Modify: `src/llm_bus/cli/hooks.py:hook` (turn counter)
- Modify: `src/llm_bus/cli/guidance.py:_delegation_text` (gate lines only with gates; owner-decisions line)
- Test: `tests/coordination/test_decisions.py`; `tests/coordination/test_delegation.py` (D24 non-empty cases)

**Read first:**

- `src/llm_bus/cli/hooks.py:hook` — UserPromptSubmit path after `store.register`.
- `src/llm_bus/coordination/store.py:create_delegation_tables` — `coordination_owner_turns`, `coordination_decisions` (Task 2).

**Current-state evidence:**

- No turn identity is stored. Hooks write agent registration and guidance state, and `cli/guidance.py:guidance` also reaps due lock checks and dispatches their wakes through `locks/summary.py:summary` (N3 correction).
- PR1 requires at least one gate in every stored delegation and binding (SC1 "Non-empty rule"): `Delegation.parse` raises `Invalid delegation gates` for an empty `gates` object; `from_specs` raises `Delegation needs at least one GATE:BRANCH`; `set_project_delegation` deletes a binding when `not kept.gates`; `_delegation_text` prints the coordinator gate line whenever a project delegation exists and the worker gate line only when `granted.gates`. No current test asserts these errors (`grep` of `tests/` at `8f4b1fa`), so no existing test text changes.

**Intended edits:**

- `decisions.record_turn(store, address)` — called from `hook` on `UserPromptSubmit` for non-subagent sessions; increments `coordination_owner_turns` only when the address is an enrolled coordinator (one indexed lookup; no write otherwise).
- D24 non-empty rule (C1 option B, decided): a delegation or binding is valid when `items()` is non-empty; `owner-decisions` may exist without any gate. The fixed floor is unchanged. Edits:
  - `Delegation.parse` keeps the `gates` key required and still a dict, and accepts an empty `gates` object only when at least one flag is set; with no gate and no flag it raises `Invalid delegation gates` as today.
  - `Delegation.from_specs` accepts an empty spec list only when the result has an item. Because of the SC1 dependency rules, the only valid gate-less inputs are `owner_decisions` alone or with `decision_default_minutes` (Task 11). With no spec and no flag it raises `BusError("Delegation needs at least one GATE:BRANCH or --owner-decisions")`.
  - `Delegation.narrow` needs no extra check: its items come from `self.items()` and the list is non-empty (`bind` calls it only when `--narrow` is given).
  - `set_project_delegation` deletes a binding only when `kept is None or not kept.items()`; a binding that keeps only `owner-decisions` stays stored.
  - `cli.create_for` (and `coordinate` through `_declare_delegation`) stores a delegation when `--delegate` or any flag is given (SC2). `setup.run_wizard` (PR1: `chosen = ... from_specs(specs) if specs else None`) builds `chosen` when specs or a flag is chosen, so an owner who disables every gate but keeps owner decisions stores a decisions-only delegation; its args keep gate specs and flags apart per SC2 (from Task 7).
  - `cli/guidance.py:_delegation_text`, coordinator (active state): when `project.gates` is non-empty, the PR1 gate line is unchanged; when it is empty, print instead `Delegated items: ITEMS; workers whose binding is narrower: N. ` with `ITEMS = ", ".join(project.items())`. Worker: the PR1 gate line stays guarded by `granted.gates`; when `granted.owner_decisions` is true, append `Owner decisions via the coordinator: accept a decision only after coordination decision-show reports accepted: true. ` (same `effective` lookup, same loop; the loop stops after the first grant that adds either line, so each line appears at most once). The rejoin line is unchanged.
  - `authority.py:_gate_decision` needs no change: a delegation with no gate denies every approval as `gate_not_delegated`. Task 13's sender and receiver checks inherit this.
  - Rationale (D24): decision records (D8) do not depend on git gates; D12 and D14 let owners disable or narrow each item independently.
- `decisions.ask(db, record, caller, *, decision_class, question, recommended: str | None, ticket: str | None, worker: str | None, answer: str | None, now) -> tuple[dict[str, object], list[Message]]` and `decisions.answer(db, record, caller, decision_id, answer, now) -> tuple[dict[str, object], list[Message]]` (SC3; Task 10 returns no messages, Task 11 adds them) — caller is current coordinator. `worker`, when given, must be an enrolled worker address of the current generation; it names the blocked worker (Task 11 wakes it). `ask` with `answer` stores the row directly as `answered` through the same code path as `answer` (D23/P4); there is no separate record action. An answer is stored verbatim with `source_session=caller`, `source_turn` from `coordination_owner_turns` (null if absent), `resolved_at`, `answer_sha256`; `class == "objective"` also calls `amendments.append(..., decision_id=...)` (D23/P3; no `kind`).
- `decisions.status(db, record, caller, decision_id, *, now) -> dict[str, object]` — `accepted` only if generation is current, status is answered or defaulted, caller is the coordinator or an enrolled worker whose `effective(db, record, grant_id, now=now).owner_decisions` is true, and `worker` is null or equals caller (else `wrong_session`).
- CLI actions `decision-ask` (`--class --question [--recommended] [--ticket] [--worker] [--answer]`), `decision-answer` (`--decision-id --answer`), `decision-show` (`--decision-id`), `decision-list`, each dispatched from `run` per SC3 and returning its own result; `coordinate`/`amend` `--owner-decisions` (SC2 replace and resume semantics); `join --narrow owner-decisions`.

**Interfaces:**

- Consumes: `amendments.append`, `effective`.
- Produces: `decisions.*`, `Delegation.owner_decisions`, CLI actions (Task 11 consumes `ask` and pending rows).

**Dead-code scope:** new module and hook call; confirm hook output unchanged for unenrolled sessions.

**Review target:** provenance fields; workers cannot accept without bound item; wrong-session rejection; hook write limited to enrolled coordinators; one validation path for answers (no `record_direct`); class alone decides amendment and default eligibility.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests for each command, provenance fields, turn increments only for enrolled coordinator, acceptance matrix; answered `objective`-class decision appends an amendment and `scope`-class does not; `decision-ask --answer` stores `answered` with the same provenance fields as `decision-answer`; `--worker` naming a non-enrolled address rejected; `--kind` and `decision-record` are not accepted by the parser; `amend` adding `--owner-decisions` lists a joined worker in `rejoin_required` and its `decision-show` reports `accepted: false` until it rejoins; resume with a differing `--owner-decisions` raises `Delegation differs`. D24 non-empty tests: `coordinate --owner-decisions` without `--delegate` stores a delegation whose `items()` is `("owner-decisions",)` and whose `wire()` has `"gates": {}`; that wire round-trips through `parse`; `parse` of `{"version": 1, "gates": {}}` with no flag still raises `Invalid delegation gates`; `from_specs([])` with no flag raises the non-empty error; `join --narrow owner-decisions` alone binds only that item; a worker bound only to `owner-decisions` gets `accepted: true` for an answered decision and `Direction denied: gate_not_delegated` for any `approve-*`; its guidance has no gate line and has the owner-decisions line; a coordinator with a decisions-only delegation gets the `Delegated items:` line; `amend --delegate merge:develop --owner-decisions` followed by `amend --owner-decisions` removes the gates and keeps the worker's binding with `owner-decisions`; `amend --no-delegation` still deletes every binding; a wizard run that disables every gate and keeps owner decisions stores the decisions-only delegation.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_decisions.py -q`
  - Expect: fails.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/local -q`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; empty.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review.
  - Record: commit `feat(coordination): record owner decisions with provenance`.
  - Cleanup: none.

### Task 11: Timed decision defaults and digest (PR4)

**Goal:** At the deadline, apply recommended options to eligible unanswered decisions, wake the coordinator and the blocked worker, and list the defaults in a digest.

**Delivers:** F6 (defaults), F3 (profile items), F8 (PR4 docs)

**Execution:**

- Prerequisites: Task 10.
- Wave: 10
- Mode: sequential-only.
- Isolation: PR4 branch.
- Integration order: second in PR4.

**Files:**

- Modify: `src/llm_bus/coordination/decisions.py`
- Modify: `src/llm_bus/coordination/delegation.py:Delegation` (field `decision_default_minutes`, 1–1440, requires `owner_decisions`; item `decision-defaults`; per SC1)
- Modify: `src/llm_bus/coordination/cli.py` (`run`: SC4 lazy application and SC3 delivery; `amend`/resume/`create_for` flag per SC2), `src/llm_bus/cli/commands.py:human_command` (hidden timer), `src/llm_bus/cli/guidance.py:_coordination` (lazy application and digest line), `src/llm_bus/coordination/setup.py`, `src/llm_bus/coordination/store.py:show`
- Test: `tests/coordination/test_decision_defaults.py`
- Docs: rule file, skill reference, README.

**Read first:**

- `src/llm_bus/locks/timer.py:schedule_check`, `detach`, `run_timer`; `src/llm_bus/locks/notices.py:deliver` — detached timer and wake pattern.
- `src/llm_bus/locks/summary.py:summary` — hook-time lazy application precedent: `BEGIN IMMEDIATE`, `reap`, `persist`, commit, then `deliver`.
- `src/llm_bus/cli/commands.py:human_command` `lock timer` branch — hidden timer routing: `if not detach(): run_timer(...)`.
- `tests/locks/test_checks.py` — `run_timer(store, key, sleep=fake_sleep)` and the monkeypatched `llm_bus.locks.notices.dispatch_pending` recorder pattern for asserting wake recipients.

**Current-state evidence:**

- Lock checks use a detached `llm-bus lock timer` process (`schedule_check` runs `[sys.executable, "-m", "llm_bus", "lock", "timer", scope, resource]`; `human_command` calls `detach` then `run_timer`) plus lazy checks on later commands.
- `run_timer` sleeps in `TIMER_POLL_SECONDS` slices until the deadline, applies the change in `BEGIN IMMEDIATE`, then calls `notices.deliver(store, messages)`, which calls `dispatch_pending(store, recipient, trigger_id=message_id)` per message and reports a raised dispatch as wake status `unknown`.
- `dispatch_pending` claims a wake lease and requests up to two native host turns (`MAX_HOST_ATTEMPTS`).
- Hooks already apply due lock checks and dispatch wakes: `cli/guidance.py:guidance` calls `Locks.summary`, and `locks/summary.py:summary` runs `reap(db)` and `persist(...)` in `BEGIN IMMEDIATE`, then `deliver(store, sent)` after commit, on every hook. `locks/timer.py:schedule_check` documents the pairing: "Without a timer, the next lock command or hook of any session raises the check."
- `store.project(db, scope, label)` resolves a project by scope and label; the lock timer passes its scope explicitly on the command line.
- `decisions.status` (Task 10) accepts `answered` or `defaulted` for a worker when `effective(..., now=now).owner_decisions` is true; it does not look at `decision-defaults`.
- `coordination approve` bypasses `coordination/cli.py:run`: `cli/app.py:main` routes it to `approvals.approve`, which sends through `commands.send` and `Store.send` in its own transaction (SC4 evidence).
- `writes.send_in_transaction` accepts `sender == recipient` and calls `request_dispatch` for the recipient.

**Intended edits:**

- `decisions.ask` sets `deadline_at = now + minutes` only when the decision is unanswered, `class == "scope"`, `recommended` is given and the project delegation has `decision_default_minutes` (no `kind`, D23/P3); after commit the CLI calls `schedule_default(project_label, decision_id)`, which runs `[sys.executable, "-m", "llm_bus", "coordination", "decision-timer", LABEL, "--decision-id", ID]` with the same `subprocess.run` options and error suppression as `schedule_check`. `LABEL` only satisfies the `coordination` parser's `project` positional; the timer never resolves the project from it.
- `decisions.run_decision_timer(store, decision_id, *, sleep=time.sleep) -> None` — mirrors `run_timer`: load the row by its globally unique `decision_id` and the project record by the row's `(scope, project_id)`, so the timer does not depend on the detached process's folder or on the label; re-read the row each loop; return when it is missing, no longer `pending`, or has no `deadline_at`; sleep `min(remaining, TIMER_POLL_SECONDS)` while before `deadline_at`; then `BEGIN IMMEDIATE`, `messages = apply_due(db, record)` with the project record re-read inside that transaction, commit, `locks.notices.deliver(store, messages)`, and return (one application attempt; a row that `apply_due` skipped stays `pending` for an owner answer) so the coordinator and the blocked worker each get a wake attempt at the deadline (D22). `human_command` routes hidden `coordination decision-timer` like `lock timer` (`if not detach(): run_decision_timer(...)`).
- `decisions.apply_due(db, record) -> list[Message]` — rechecks eligibility against the current state on every call (M1; D5, D9, D14), because `ask` decided it only once:
  - when `record["revoked_at"]` is set, return `[]`;
  - `project = project_delegation(db, record)`; when it is `None` or lacks `owner_decisions` or `decision_default_minutes`, clear `deadline_at` on `pending` rows of `generation = record["generation"]` and return `[]` (no notice, no wake). Re-enabling decision defaults later therefore never defaults a decision asked while they were enabled; such a row waits for an owner answer. Every caller passes the current record, and `amend` is itself an SC4 lazy caller, so `amend` removing and then re-adding defaults still clears the deadline in between;
  - otherwise select only `status='pending'` rows with `generation = record["generation"]` and `deadline_at <= now`. Rows of an older generation (after `replace`) are never defaulted.
  For each selected row: set `status='defaulted'`, `answer=recommended`, `source_session='default'`, `resolved_at`; then for each row send:
  - to the coordinator, a self-addressed notice `send_in_transaction(db, coordinator, coordinator, body, cross_folder=True)` with `body = f"Decision {decision_id} took its recommended option by default: {recommended}. Report it to the owner."`;
  - to `row.worker`, when set and still an enrolled worker of the current generation, a notice from the coordinator `send_in_transaction(db, coordinator, worker, body, cross_folder=True)` with `body = f"Decision {decision_id} was answered by default: {recommended}. Run coordination decision-show {decision_id}; act on it only if it reports accepted: true."`.
  Notices are plain messages; they carry no direction or approval. Callers, each inside its own `BEGIN IMMEDIATE`, each passing the returned messages to `locks.notices.deliver` after commit:
  - the timer;
  - the coordination CLI actions listed in SC4, inside `run`'s transaction, delivered after commit through the SC3 path (`approve` is not a lazy caller; SC4);
  - coordinator guidance: `cli/guidance.py:_coordination`, for a session enrolled as the current coordinator of a project with `decision_default_minutes`, opens `BEGIN IMMEDIATE`, calls `apply_due`, commits, then calls `deliver`, following `locks/summary.py:summary`. It then reads the defaulted count for the digest line. Worker and unenrolled guidance never call `apply_due`.
- `decisions.status` (Task 10) — M1 / D2: for a `defaulted` row, a worker caller gets `accepted: true` only when `effective(db, record, grant_id, now=now).decision_default_minutes` is set; otherwise `accepted: false` with reason `default_not_bound`. `answered` rows and coordinator callers are unchanged. Per SC1, `intersect` keeps the project's minutes only when the binding also has the item, and a `--narrow` list without `decision-defaults` drops it.
- Digest: `decision-list --defaulted`; `show` key `decision_digest` (defaulted rows for current generation) only when non-empty; coordinator guidance line `N decisions were taken by default; report them to the owner (see decision-list --defaulted).`
- Docs: `decision-ask --worker ADDRESS` names the worker that is waiting, so a default wakes it; without `--worker` only the coordinator is woken. Rule text for coordinators: "Classify decisions conservatively; when a question touches security, cost, data or an irreversible action, or you are unsure, use that class, never `scope`." A worker narrowed away from `decision-defaults` waits for an owner answer.
- CLI `coordinate`/`amend` `--decision-defaults MINUTES` (SC2 replace and resume semantics); setup profile adds `owner-decisions` and `decision-defaults` under `flags` (`--owner-decisions --decision-defaults 30` in `command`), and `run_wizard`/`create_for` pass both flags (SC2).

**Interfaces:**

- Consumes: `locks.timer.detach`, `TIMER_POLL_SECONDS`, `locks.notices.deliver`, `send_in_transaction`, `decisions.ask`, `decisions.status` and `coordination_decisions.worker` (Task 10).
- Produces: `decisions.apply_due`, `run_decision_timer(store, decision_id, *, sleep=)`, `schedule_default`, hidden action `decision-timer`, `Delegation.decision_default_minutes`, status reason `default_not_bound`.

**Dead-code scope:** reuse `locks.timer.detach`, `TIMER_POLL_SECONDS` and `locks.notices.deliver`; do not copy timer or wake code beyond the subprocess call and the poll loop. Check no duplicate notice or wake helper (`grep -rn "def deliver" src/llm_bus/coordination` returns nothing; wakes go through `locks.notices.deliver` or the existing `dispatch_pending` CLI call).

**Review target:** only class `scope` defaults; every `apply_due` caller rechecks the current generation, revocation and project delegation (no default after `amend` drops decision defaults, `replace` or revoke); single application under concurrent timer and lazy paths (`BEGIN IMMEDIATE` and `status='pending'` guard), so notices and wakes are sent once; at the deadline both the coordinator and the named enrolled worker are notified and woken through `deliver`; no wake for a worker that left or was revoked; only coordinator guidance applies defaults from a hook, and it delivers wakes after commit like `locks/summary.py`; a narrowed worker never sees a default as accepted; the timer resolves the project from the decision row; notices grant nothing.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests with frozen clock and a `dispatch_pending` recorder patched at `llm_bus.locks.notices.dispatch_pending`:
    - `run_decision_timer` with a fake `sleep` applies an eligible default at the deadline and records wake attempts for exactly the coordinator and the decision's worker; each has one new inbox notice naming the decision.
    - Decision without `--worker`: only the coordinator is woken.
    - Worker revoked before the deadline: only the coordinator is woken and no worker notice is stored.
    - Lazy path (`decision-show` after the deadline, timer never ran) applies once and wakes both; a later timer run applies nothing and dispatches nothing. `coordination show` after the deadline also applies once and returns the `show` result with `wakes`.
    - Resume with a differing `--decision-defaults` raises `Delegation differs`; `amend` adding `--decision-defaults` lists a joined worker in `rejoin_required`.
    - Dispatch raising an error reports wake `unknown` and leaves both notices pending.
    - Each ineligible class unchanged; answering before the deadline prevents the default, and the timer exits without notices.
    - Coordinator `UserPromptSubmit` hook after the deadline (timer never ran) applies the default once and wakes both; a later timer run applies nothing and dispatches nothing; a worker hook after the deadline applies nothing.
    - `run_decision_timer` run from a working directory outside the project's folder still finds and applies the decision.
    - Worker that rejoined with `--narrow` dropping `decision-defaults` gets `accepted: false` (`default_not_bound`) for the defaulted decision, and `accepted: true` for a decision the owner answered.
    - Digest lists the default.
    - M1, each before the deadline: (a) `amend` without `--decision-defaults`; (b) `replace` with a fresh generation; (c) project `revoke`. After the deadline, `run_decision_timer`, `decision-show` and the coordinator `UserPromptSubmit` hook each apply nothing, store no notice and dispatch no wake; the row stays `pending`; the timer returns. For (a), a later `amend` that re-adds `--decision-defaults` does not default the old row.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/coordination/test_decision_defaults.py -q`
  - Expect: fails.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/coordination tests/locks tests/local -q`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; empty.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review.
  - Record: commit `feat(coordination): apply timed decision defaults`; open PR4.
  - Cleanup: none.

### Task 12: Relay approval transport (PR5)

**Goal:** Let the relay carry approval directions on the coordination route after capability negotiation.

**Delivers:** F7 (relay)

**Execution:**

- Prerequisites: PR4 merged (merge order only; no code dependency).
- Wave: 11
- Mode: parallel-safe with Task 13 (owns only `cloudflare/`).
- Isolation: worktree `remote-gates-relay` off PR5 branch.
- Integration order: first in wave 11.

**Files:**

- Create: `cloudflare/migrations/0007_coordination_approvals.sql` (`ALTER TABLE messages ADD COLUMN approval TEXT;`)
- Modify: `cloudflare/src/messages.ts:direction`, `send`, `inbox`; `cloudflare/src/index.ts` route table; `cloudflare/src/types.ts`
- Test: `cloudflare/test/relay.test.ts`

**Read first:**

- `cloudflare/src/messages.ts:direction` — exact five-key check and six-operation list.
- `cloudflare/migrations/0006_coordination_availability.sql` — additive column precedent.

**Current-state evidence:**

- `direction()` rejects any operation outside the six; no `approval` field exists; `GET /v2/coordination/capabilities` returns `{version:1}`.

**Intended edits:**

- Add route `GET /v2/coordination/approvals/capabilities` → `{approval_version: 1}`; keep the existing capability response unchanged.
- `direction()` accepts the four approval operations; `approval()` validates exact keys per operation (same rules as `Approval.validate`, except that `record_id` is never accepted: gate records are local-only); `send` requires `approval` iff operation is an approval operation, only on the `coordination` class; stores it; idempotency comparison includes it; `inbox` returns it when present.

**Interfaces:**

- Produces: wire contract `{"coordination": {...five keys...}, "approval": {...}}` consumed by Task 13.

**Dead-code scope:** relay only; none.

**Review target:** old operations and response unchanged; approval cannot ride ordinary or availability classes; idempotency.

- [ ] **Step 1: Establish the pre-change check**
  - Check: relay tests for accepted approval, rejected approval on wrong class, missing approval, extra keys (including `record_id`), unchanged v1 capability.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `cd cloudflare && bun run test`
  - Expect: new tests fail.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: none.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `cd cloudflare && bun run check && bun run test`
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 5: Run the task gate**
  - Run: same.
  - Expect: pass.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent review.
  - Record: commit `feat(relay): carry coordination approvals`.
  - Cleanup: none.

### Task 13: Client remote approvals and peer policy cap (PR5)

**Goal:** Send remote approval directions only while the coordinator owner's own project delegation covers them, and admit them only when the receiver's project delegation, worker binding and peer policy all allow the gate.

**Delivers:** F7 (client), F8 (PR5 docs)

**Execution:**

- Prerequisites: Task 12 wire contract (defined above; can be built concurrently and integrated after Task 12).
- Wave: 11
- Mode: parallel-safe with Task 12.
- Isolation: worktree `remote-gates-client`; owns `src/`, `tests/remote/test_remote_approvals.py`, `tests/coordination/test_approvals.py`, docs.
- Integration order: second; this task's owner resolves conflicts.

**Files:**

- Modify: `src/llm_bus/policy/config.py:PeerPolicy`, `_peer` (key `accept_delegated_gates`, subset of `GATES`, unique, default empty)
- Modify: `src/llm_bus/remote/outbox.py:enqueue_in_transaction` (envelope `approval`), `src/llm_bus/remote/outgoing.py:upload` (`supports_approvals`, `unsupported_approvals`, sender-side delegation check; `permits_outgoing` unchanged, L2), `src/llm_bus/coordination/delivery.py:outgoing` (sender-side check at enqueue), `src/llm_bus/remote/http.py`, `src/llm_bus/remote/protocol.py` (parse `approval`), `src/llm_bus/remote/incoming.py` (class check), `src/llm_bus/remote/admission.py` (parse, authorize with approval, persist)
- Modify: `src/llm_bus/coordination/authority.py:_gate_decision` (remote branch: intersect with peer cap instead of `remote_gate_unsupported`; extract `_delegation_reason`), add `sender_gate_reason`
- Modify: `src/llm_bus/coordination/cli.py:add_parsers` (`setup --delegate`), `run` (`setup` stores the delegation after `_create`), `_transition` (`amend` guard for remote-coordinated projects, M2)
- Modify: `src/llm_bus/cli/commands.py:send` (remove PR1 remote approval rejection)
- Modify: `tests/coordination/test_approvals.py` (added by PR1; replace the remote-rejection expectation)
- Test: `tests/remote/test_remote_approvals.py`
- Docs: `docs/remote-bus.md` optional project directions; `skills/llm-bus/references/remote.md`, `coordination.md`; README.

**Read first:**

- `src/llm_bus/remote/outgoing.py:supports_availability`, `supports_coordination` — negotiation pattern.
- `tests/remote/test_coordination.py:test_policy_intersection_and_independent_store` — policy intersection test pattern.

**Current-state evidence:**

- `PeerPolicy` has four capabilities; `_keys` rejects unknown keys.
- `_gate_decision` denies remote coordinators (Task 3).
- The remote send path never authorizes on the sender machine: `cli/commands.py:send` calls `delivery.outgoing` (target lookup and `require_coordinator` only), then `remote/outgoing.py:enqueue_remote`, which checks only the repository and the peer's `send_messages`.
- `remote/outgoing.py:permits_outgoing` checks the `coordination` key with `require_coordinator` only, then `send_messages` and scope. `remote/batches.py:sync_outgoing` skips (`continue`) any envelope `permits_outgoing` refuses (`batches.py:125`, before it splits upload from status at `:127`), and `poll_page` calls it again for status items (`batches.py:107`); `outgoing.upload` calls it once more (`outgoing.py:131`) and raises `RelayError("unsupported_coordination")` before upload when the relay lacks the capability (`outgoing.py:126-127`). A check inside `permits_outgoing` would therefore also suppress status updates.
- Receiver side (M2): `setup` inserts through `_create`, and `coordination_projects` has `UNIQUE(scope,label)` (`store.py:23`), so a second `setup` fails. `amend` requires `record["coordinator"] == address` and a local coordinator enrollment (`cli.py:306-316`), which no local session has when the coordinator is `MACHINE/REPO/NATIVE`. The documented remote replacement path is a receiver-local `coordination replace --coordinator MACHINE/REPO/NATIVE` (`docs/remote-bus.md` "Optional project directions"; `skills/llm-bus/references/coordination.md` "Revocation, replacement and recovery"), which moves the project to a new generation; the delegation is keyed by generation (`delegation.py:123-132`), so today the receiver has no delegation after it.

**Intended edits:**

- Remote branch of `_gate_decision`: parse coordinator `MACHINE/REPO/NATIVE`; `config.peer(repo_id, machine)`; effective gates further intersected with `accept_delegated_gates` (gate-level only; flags such as `auto_merge` stay local and are irrelevant to remote approvals); missing config or peer → `gate_not_delegated`.
- Receiver lifetime cap (review-t3-a1 L3): a peer cannot widen the local 24 h approval limit. `admission` refuses (`invalid_direction`, not admitted) a remote approval whose `expires_at` is more than 24 h after the local admission time, before `authorize` and `persist`. Local approvals keep the PR1 cap in `approvals.parse_duration`.
- Sender side (H1; AGENTS.md "Sender and receiver permissions must both allow the operation"; D1/D2): extract from `_gate_decision` a private `_delegation_reason(project: Delegation | None, granted: Delegation | None, gate: str, approval: Approval, now) -> str | None` holding PR1's ordered checks with PR1's sets (R1; security-relevant): `gate_not_delegated` when `project` or `granted` is `None` or `granted.targets(gate)` is empty; `target_denied` when `approval.base` is not in `granted.targets(gate)`; `target_denied` when `approval.branch == approval.base` or `approval.branch` is in `project.all_targets()` (the project set, never `granted`, so a narrower binding or peer cap cannot let a work branch equal another target); `expired`. `_gate_decision` calls it with `(project_delegation(...), granted, ...)`, where `granted` is `effective(..., now=now)` for a local coordinator and that set further intersected with the peer cap for a remote one, keeping its denial order. Add `authority.sender_gate_reason(db, *, scope: str, direction: Direction, approval: Approval, now) -> str | None`: load the project by `(scope, direction.project_id)`; return `gate_not_delegated` when the project is missing or revoked or `direction.generation` is not current; otherwise `project = project_delegation(db, record)` and return `_delegation_reason(project, project, GATE_OPERATIONS[direction.operation], approval, now)`. It uses the coordinator machine's own project delegation only; the worker binding, claim and peer cap live on the receiver.
  - Enqueue: `delivery.outgoing(..., approval=)` — when `recipient` contains `/` and `approval` is given, call `sender_gate_reason`; a reason raises `BusError(f"Direction denied: {reason}")` before anything is queued. Local recipients are unchanged (the local `authorize` gate branch already reads the same project delegation).
  - Upload: `outgoing.upload` only (L2), never `permits_outgoing`, so neither status call site (`batches.py:107`, `:125`) is affected and status updates for approvals already uploaded before a narrowing still arrive (plan-review-c1r2 N1). In `upload`, next to the `unsupported_coordination` check and before the relay request: when the `coordination` operation is in `GATE_OPERATIONS`, raise `RelayError("unsupported_approvals")` unless `supports_approvals` succeeds; then require `envelope["approval"]`, `Approval.parse` it, and return `False` when it is missing or invalid or `sender_gate_reason(...)` returns a reason. The item stays `queued_local`, so an `amend` that narrows the delegation, a `replace`, or expiry holds already queued approvals. Held approvals stay in the outbox like other refused items; the remote docs state this (N2).
- Enqueue stores `approval` in the queued envelope (`outbox.enqueue_in_transaction`) whatever the relay supports; capability is checked at upload time, like `unsupported_coordination` (L4). An unsupported relay leaves the item queued with `unsupported_approvals`; the approval is never stripped.
- Receiver-owner delegation (M2; D5, D14, D18; AGENTS.md "each endpoint controls its own capabilities"): on the receiver, the project delegation for a remote coordinator is the receiver owner's own local record. Commands:
  - `coordination setup ... --delegate GATE:BRANCH` (gate specs only) stores it in the same transaction as `_create`, through `Delegation.from_specs` and `set_project_delegation`.
  - `coordination amend PROJECT --delegate GATE:BRANCH | --no-delegation`: in `_transition` `amend`, when `"/" in record["coordinator"]`, skip the local-coordinator check (no local session can be that coordinator; the command carries the same receiver-owner authority as `setup` and `replace`) and accept only a delegation change: any delegation flag (`--auto-merge`, `--owner-decisions`, `--decision-defaults`) or `--objective` raises `BusError("Remote-coordinated project: amend accepts only --delegate or --no-delegation")`, because those records stay on the coordinator machine. The SC2 replace semantics and `rejoin_required` apply unchanged; narrowing applies at once through `set_project_delegation` (D14), widening lists bound workers in `rejoin_required`. Local projects keep PR1's check.
  - After a receiver-local `replace` (new generation, no delegation, no bindings, D5), the receiver owner runs `amend --delegate ...`, then each worker's owner rejoins (`join` binds the new delegation). Docs (`docs/remote-bus.md` "Optional project directions", `skills/llm-bus/references/coordination.md` replacement section) state this step and that the coordinator owner re-declares its own side with `amend` on the coordinator machine.
  - Rejected alternative: `replace --delegate` covers only the post-replace case and still leaves no way to narrow mid-generation.
- Admission persists the approval in `coordination_approvals` with the direction in the same transaction, after `receive_mode == "inbox"` and `authorize(..., approval=...)` pass. No schema change: the table exists since version 13.
- Admission and outbox reject an approval carrying `record_id` (`invalid_direction`, not admitted; outbox refuses to enqueue it), so a record-bound approval never crosses machines.
- Gate records, decisions, instructions and auto-merge stay local; docs say so. A remote coordinator cannot run `gate-accept` because acceptance requires the local enrolled coordinator.

**Interfaces:**

- Consumes: Task 12 wire contract, `Approval.parse`, `persist(..., approval=)`, `delegation.project_delegation`, `delegation.effective(..., now=)`, `GATE_OPERATIONS`.
- Produces: `PeerPolicy.accept_delegated_gates`, `supports_approvals`, `authority.sender_gate_reason`, `coordination setup --delegate`, receiver-owner `coordination amend` on remote-coordinated projects.

**Dead-code scope:** remove the PR1 `Remote approval directions are not supported` branch in `commands.send` and its expectation in `tests/coordination/test_approvals.py` (added by PR1 in this flow, so not a pre-flow test); remove `remote_gate_unsupported` reason if no longer produced. The gate, target and expiry checks exist once, in `_delegation_reason`; `sender_gate_reason` must not copy them (check: `grep -n "target_denied" src/llm_bus/coordination/authority.py` shows the reason only inside `_delegation_reason`).

**Review target:** receiver-owner `amend` on remote-coordinated projects accepts only gate changes and cannot touch local projects; sender and receiver both allow (coordinator delegation checked at enqueue and at every upload attempt; receiver three-way intersection at admission and exposure); default deny; denied-peer bypass (sender skipping its client, forged approval without delegation, monitor mode) never reaches inbox or wake; old relay leaves queued.

- [ ] **Step 1: Establish the pre-change check**
  - Check: tests: verified remote approval with all three allowing; denied when peer cap empty, when receiver project lacks delegation, when binding narrowed; monitor-mode peer never admits to inbox; crafted envelope with approval but non-approval operation rejected; crafted envelope whose approval carries `record_id` not admitted; old relay leaves `unsupported_approvals`. Sender side, with a receiver that would allow everything: coordinator delegation lacks the gate → `coordination approve` raises `Direction denied: gate_not_delegated` and nothing is queued; base not in the coordinator's targets → `Direction denied: target_denied`; delegation narrowed with `amend` after enqueue → `sync_outgoing` makes no relay request for the item and it stays queued; a queued gate envelope without `approval` is never uploaded; an uploaded approval still receives its delivery-status update after a narrowing (N1). R1: with the coordinator's project delegation `merge:develop` and `merge:release`, and the receiver's peer cap or binding narrowed to `merge:develop`, an approval with base `develop` and branch `release` is denied `target_denied` on the receiver and refused at enqueue on the sender. L3: a remote approval expiring more than 24 h after admission is not admitted. L2 (review-t3-a1): `RemoteStore.admit_message` with a gate-op `coordination` envelope and no `approval` in inbox mode returns `None` and writes no `messages` row; the same envelope from a monitor-mode peer is stored only in `remote_monitor` and never reaches the inbox or a wake. C2R1 L2: with the sender delegation narrowed, `sync_outgoing` still applies delivery status for an already uploaded approval (both status call sites) and makes no upload request for a queued one. M2: on the receiver, after `replace` to a fresh generation without `amend`, a remote approval is denied `gate_not_delegated`; after `amend --delegate merge:develop` and the worker's rejoin, the same approval verifies; `amend` narrowing on the receiver denies an approval for the removed gate at once; receiver `amend --auto-merge` and `amend --objective` on a remote-coordinated project are rejected; `amend` on a local project by a non-coordinator is still rejected; `setup --delegate merge:develop` stores the delegation and `join` binds it.
  - Cleanup: none.
- [ ] **Step 2: Prove the check exposes the intended gap**
  - Run: `uv run pytest tests/remote/test_remote_approvals.py -q`
  - Expect: fails.
  - Cleanup: none.
- [ ] **Step 3: Implement the minimum behavior**
  - Change: edits above.
  - Cleanup: remove obsolete PR1 remote rejection branch and reason.
- [ ] **Step 4: Prove the focused behavior passes**
  - Run: `uv run pytest tests/remote tests/coordination -q`
  - Expect: pass.
  - Cleanup: `grep -rn remote_gate_unsupported src tests` returns nothing.
- [ ] **Step 5: Run the task gate**
  - Run: `uv run poe check`, then `TESTS_DIFF`.
  - Expect: pass; diff lists at most `tests/coordination/test_approvals.py`.
  - Cleanup: none.
- [ ] **Step 6: Review and record the task**
  - Review: independent security review of remote access per AGENTS.md.
  - Record: commit `feat(remote): admit delegated approvals under peer policy`; open PR5 listing the single post-PR5 owner rollout ("Post-merge owner actions": relay migration, Worker deploy, `llm-bus upgrade`, `llm-bus install`, skill and rule copies, in that order).
  - Cleanup: none.
