# Package organization and deep refactor plan

## Features and effects

This plan reorganizes the existing bus without changing commands, stored data, permissions, or message delivery. It replaces the flat Python module collection with one feature-oriented package and decomposes the largest responsibility owners. Aim for 150–250 lines per implementation file, with roughly 200 as the normal target. A file above 300 lines requires a written cohesion justification at its task review. These are review targets, not enforced limits or reasons to fragment an atomic operation.

Status: implemented under semantic revision `adopted-1`. All seven task reviews, final plan conformance, aggregate verification, and independent aggregate review passed. Verification covered 294 Python cases, 37 relay cases, static checks, isolated package imports and entrypoints, and the relay dry-run build. Baseline: `8c0b8a5` plus the initial mailbox diagnostics extraction.

Execution-status note: task instructions, unchecked step lists, baseline findings, and the final historical review section below preserve the adopted planning record. They are not outstanding work or current runtime instructions. The pull request records delivery verification and subsequent review-loop results.

### F1: Navigable Python package

- Problem: 22 flat application modules encode features in filename prefixes. Packaging duplicates their names, while package smoke discovery assumes `src/*.py`.
- Proposed behavior: related code lives under `src/llm_bus/`; `llm-bus` and `python -m llm_bus` retain their existing commands and output.
- Scope: internal imports migrate together. Preserve demonstrated runtime interfaces; do not keep dozens of old module shims without an identified external consumer.
- Acceptance: isolated wheel imports cover every nested module; core help works without the collaboration extra; module execution works outside the checkout.
- Execution mapping: Task 1.

### F2: Smaller owners of persistence and delivery

- Problem: mailbox, lock, collaboration state, and remote synchronization classes combine several independent responsibilities.
- Proposed behavior: small facades coordinate plain functions that own cohesive workflows. SQL helpers receive the active connection when operations must commit together.
- Scope: preserve transaction boundaries, schema versions, migrations, paths, byte formats, error types/messages, return shapes, ordering, and live permission checks.
- Acceptance: existing rollback, replay, revocation, concurrency, reply ownership, and wake tests retain their behavioral assertions; no replacement code remains duplicated.
- Execution mapping: Tasks 2–5.

### F3: Focused interfaces, relay, viewer, and tests

- Problem: CLI, owner UI, relay, viewer, and several test files combine unrelated change domains. The largest tests are 1,722 and 2,245 lines.
- Proposed behavior: group tests and implementation by feature, preserve fixture isolation, and keep security checks beside the boundary they protect.
- Scope: relay deployment, production state, credentials, and user hook configuration are unchanged. SQL migration history remains in place.
- Acceptance: Python and relay gates pass; every original test is accounted for; embedded Python integration programs use the new imports and still forbid native wake.
- Execution mapping: Tasks 1, 6–7.

## High-level design and rationale

Use feature folders, plain functions, and the existing SQLite interfaces. No new runtime dependency, dependency-injection framework, generic repository layer, inheritance hierarchy, mixins, or plugin system is needed. A facade is justified where existing callers benefit from one stable operation owner; it must not become a second implementation.

Dependencies flow from commands and runtime orchestration into feature operations, then into policy/protocol and connection-level functions. Low-level types, errors, and routing must not import CLI or runtime orchestration. Mailbox pending admission may consult collaboration exposure and remote policy through their explicit policy owners; neither owner may import the mailbox facade merely to obtain shared types/errors.

The user's folder-reorganization request is a scoped override of the current flat-`src` convention. Update `AGENTS.md:Repository workflow` and `README.md:Development` during Task 1. Existing design plans are historical evidence, not files to rewrite mechanically.

### Architecture findings

These are structural findings, not established correctness defects:

| Priority | Current evidence | Change justified by the evidence |
| --- | --- | --- |
| First | `llm_bus_store.py:Store` is 796 lines of module code after the first extraction | Separate connection, agent lifecycle, writes, pending admission, and history |
| First | `llm_bus_collab_state.py:CollabState` shares an 860-line module with schemas, invite parsing, grant policy, and exposure checks | Separate wire/policy contracts, persistence, and mailbox admission |
| First | `llm_bus_remote.py:Bridge` and `llm_bus_remote_store.py:RemoteStore` occupy 704/636-line modules | Separate HTTP/protocol, durable scheduling, outgoing state, admission, and orchestration |
| Next | `llm_bus_locks.py:Locks` shares 768 lines with catalog, lifecycle SQL, timers, and notices | Separate catalog/lifecycle/timer/rendering without changing transaction ownership |
| Next | `llm_bus.py:main`, `OwnerPage`, and `_Handler` mix parsing, rendering, actions, and lifecycle | Split by workflow and security boundary |
| Next | `test_bus.py` and `test_bus_remote.py` mix many behavioral domains | Move tests into feature folders and scope fixtures carefully |
| Bounded | Relay `index.ts` is 341 lines; viewer is 339 lines | A few cohesive modules suffice; avoid a file per tiny helper |

### Destination and ownership map

Paths below are relative to `src/llm_bus/` unless another root is shown. Each listed module owns the named behavior, not merely a slice of lines. Empty package initializers are acceptable; avoid eager imports with runtime side effects.

| Current source | Proposed destinations and responsibilities |
| --- | --- |
| `src/llm_bus.py` | `__init__.py` exports `main`; `__main__.py` invokes it; `cli/app.py` dispatches identity-free versus registered operations; `cli/parser.py` builds argparse commands; `cli/commands.py` owns send/history/monitor/report command handling; `cli/hooks.py` owns host lifecycle; `cli/guidance.py` builds notices |
| `llm_bus_store.py` | `mailbox/store.py` preserves the `Store` facade; `connection.py` owns path/open/retry/transaction behavior; `models.py` owns mailbox row shapes and address validation; `agents.py` owns lifecycle; `writes.py` owns send/reply/ack SQL; `pending.py` owns bounded scans plus current-policy checks; `history.py` owns history/conversation queries |
| `llm_bus_schema.py` | `mailbox/schema.py` owns table definitions/version dispatch; `mailbox/migrations.py` owns legacy migration/backfill operations |
| `llm_bus_diagnostics.py` | `diagnostics/messages.py` owns body-free read-only status; `diagnostics/synchronization.py` owns retained scheduling projections and sanitization |
| `llm_bus_errors.py`, `llm_bus_routing.py` | `errors.py`, `routing.py`; no further internal split |
| `llm_bus_locks.py` | `locks/service.py` owns acquire/release/status and transaction orchestration; `lifecycle.py` owns lock rows/reap/handoff/check SQL; `catalog.py` owns names/similarity/catalog; `notices.py` owns notice text; `timer.py` owns scheduling/process lifecycle; keep small lock records with their principal consumer unless a shared `models.py` earns its cost |
| `llm_bus_dispatch.py`, `llm_bus_wake.py` | `wake/dispatch.py` preserves lease/coverage orchestration; `wake/adapters.py` preserves native host adapters. A cohesive 260-line dispatcher may remain; extract leases only if ownership becomes clearer |
| `llm_bus_install.py` | `hosts/settings.py` owns parse/atomic write/configure; `hosts/restore.py` owns restoration/merge after failure. CLI install/uninstall orchestration belongs in `cli/installation.py` |
| `llm_bus_policy.py` | `policy/config.py` owns strict TOML and immutable configuration records; `policy/inbound.py` owns authenticated identity/current-scope checks; `remote/credentials.py` owns token access; report validation moves to `reports/protocol.py` |
| `llm_bus_remote.py` | `remote/http.py` owns `Relay`, errors, and no-redirect HTTP; `protocol.py` owns peer/delivery/batch parsing; `bridge.py` owns runner and ordered sync pass; `discovery.py` owns publication/refresh; `outgoing.py` owns upload/status; `incoming.py` owns download/receipt/ack; `reports/sync.py` owns report upload/polling |
| `llm_bus_remote_store.py` | `remote/models.py` owns envelope/peer/delivery records and reply UUID rules; `scheduling.py` owns durable retry/freshness/horizon; `discovery_store.py` owns session publication/cache; `outbox.py` owns enqueue/parent binding/progress; `admission.py` owns replay-safe inbox/monitor admission; `monitor.py` owns retained human queries. Retain a small `RemoteStore` facade in `remote/store.py` only for the existing caller contract |
| `llm_bus_reports.py` | `reports/protocol.py` owns status/reason/deadline validation; `submissions.py` owns recipient binding/queued submissions/acceptance; `snapshots.py` owns polling/snapshot/revision rules. A thin `Reports` facade may remain in `reports/store.py` where it reduces caller churn |
| `llm_bus_collab_state.py` | `collab/models.py` owns records; `schema.py` owns DDL/upgrades; `invites.py` owns encoding/decoding; `policy.py` owns grants/effective mode; `state.py` owns connection and `CollabState` facade; `pairing_store.py` owns invites/joins; `peer_store.py` owns peer/grant/revoke updates; `message_store.py` owns messages/transitions; `exposure.py` owns live mailbox exposure. Keep daemon-file helpers with daemon startup unless a separate runtime owner is useful |
| `llm_bus_collab_daemon.py` | `collab/daemon.py` owns transport/task/retry lifetime; `pairing.py` owns authenticated pairing; `delivery.py` owns incoming classification, responder completion, and agent delivery |
| `llm_bus_collab_web.py`, `llm_bus_collab_html.py` | `collab/web/http.py` owns token/Host/Origin/body-limit checks and routing; `owner.py` owns state-changing owner decisions; `pages.py` owns page/card rendering; `layout.py` keeps escaping, CSS, script, hash, and layout together. Split cards only if pages still has distinct owners above the size target |
| Other collab modules | `collab/cli.py`, `identity.py`, `responder.py`, `transport.py`; keep internally cohesive modules of 175–227 lines rather than forcing additional splits |
| `cloudflare/src/index.ts` | Thin Worker lifecycle/router; `http.ts` owns bounded request parsing/errors; `auth.ts` owns authenticated grants; `messages.ts` owns session/message operations; `reports.ts` owns report operations. Share small wire types through their owner or a real shared `types.ts` if required |
| `plugins/herdr-history/viewer.py` | Keep executable `viewer.py` for curses/controller and `main`; `history.py` owns JSON validation/client. Keep formatting with the controller initially; manifest still launches `python3 viewer.py` |

### Tests and supporting files

Move tests by behavior, preserving test names, assertions, and parametrization. Use `tests/mailbox/`, `cli/`, `hosts/`, `wake/`, `locks/`, `remote/`, `reports/`, `collab/`, and `plugins/`. For example, split remote tests into scheduling, batching, replies, diagnostics, admission/revocation, monitor, reports, and HTTP/CLI integration. Split mailbox tests into messages, agents, history, migrations, and transaction behavior. Place shared setup in the narrowest appropriate `conftest.py`; retain root host-environment isolation. Do not add a generic fixture factory framework.

`cloudflare/test/relay.test.ts` can separate message and report cases with shared Miniflare setup. `bridge.test.ts` retains real HTTP/D1/Queue integration. Extract its two embedded Python programs into `cloudflare/test/scenarios/` when that simplifies imports and patch auditing; preserve the native-adapter prohibition in each scenario.

Keep `.editorconfig`, `.gitattributes`, `.gitignore`, `.python-version`, hooks, quality shell scripts, workflow files, lockfiles, Wrangler/Vitest/TypeScript configuration, migration SQL, examples, skill files/evaluations, and existing documentation in their established locations. Update only actual path/import/gate consumers. Retain lockfiles as generated dependency evidence; do not refactor them. Historical SQL migrations are retained history, not cleanup candidates.

## Data-flow analysis

| Flow | Proposed owner and preserved behavior | Critical failure/ordering invariant |
| --- | --- | --- |
| CLI send to local message and wake | CLI validates invocation; mailbox write uses owning transaction; dispatch runs after persistence | Sender registration, reply authorization, message insert, and dispatch request commit together; wake failure does not lose stored message |
| Lock handoff to notice | Lock service passes its transaction to mailbox write | Notice-persistence failure rolls back lock state; native wake follows commit |
| Remote upload/download | Bridge delegates protocol, outbox, and admission functions | Final send permission check follows sender publication; receive cursor remains per-pass, not durable |
| Remote admission | One transaction binds UUID replay, parent, local policy, inbox/monitor choice, insert, dispatch request | No helper opens a second transaction; monitor text never reaches inbox/wake |
| Pending exposure/wake | Mailbox pending owner shares live remote/collab permission evaluation | Recheck current policy before exposure and final wake; do not cache an allowed flag |
| Work reports | Report protocol/submission/snapshot owners | Bind authenticated recipient and original message; reject replay/extra fields; report polling remains independent from text permission and transport acknowledgement |
| Collaboration | Pairing/store/delivery/exposure owners | Single-use invite claim, revoke+cancel, expected-state transition remain atomic; immutable asking session owns first reply |
| Owner UI | HTTP security then owner action then rendering | Keep loopback/token/Host/Origin/CSP/full-mode confirmation; escape peer-controlled content |

## Execution instructions

**Tech stack:** Python 3.12, standard library/SQLite, optional Iroh, uv/Poe/Ruff/BasedPyright/Pylint/deptry/pytest; TypeScript Worker/D1/Queues, Bun/Vitest/Wrangler.

### Global constraints

- “Configuration and trust decisions belong to each machine's owner.” Preserve default-deny peer capability checks and separation of agent messaging, monitoring, and reports.
- “Use `uv` for Python commands.” “Run `uv run poe check` before pushing a pull request.”
- Work only in the existing owned worktree. No commit, push, deployment, production DB operation, or user settings modification is part of this review plan.
- Preserve CLI JSON/text shapes, error semantics, flags, exit behavior, native identities, stored addresses/paths, schema versions and migration order. No data migration is needed for a code-only move.
- Preserve no-runtime-dependencies installation and lazy optional Iroh imports.
- Refactor source and tests together; never weaken assertions or suppress quality rules to achieve shorter files.
- Apply `clean-code-tools` and `wnz-clean-code-py` during Python refactoring. Preserve the repository's quality configuration; do not add generic abstractions to satisfy size targets.
- Sender and receiver policy must both permit remote operations; grants never imply local inbox permission. Monitor/report data must remain outside agent prompts and wakes. Preserve blocked-peer history and final policy guards.

### Acceptance criteria traceability

| Requirement | Tasks | Observable verification |
| --- | --- | --- |
| Package/folder layout and installed runtime | 1, 7 | Recursive installed import check, console help and module help outside checkout |
| Roughly 200-line cohesive files | 2–7 | File-size inventory plus written reasons for >300-line exceptions; responsibility review |
| Message, policy, atomicity, replay and wake behavior | 2–5 | Existing focused suites, then full pytest with test inventory comparison |
| UI, relay, plugin and integration compatibility | 5–7 | Collab/viewer suites; `bun run check`; `bun run test` includes dry-run build |
| Every push-hook tool | Every task gate, 7 | `sh tools/quality/check-static.sh` exits 0 |
| Cleanup and no duplicated implementations | Every task, 7 | Old-path/import/string searches, packaging inventory, independent source audit |

### Execution topology

| Wave | Task | Mode | Prerequisite and integration gate |
| --- | --- | --- | --- |
| 1 | 1: package/runtime migration | sequential-only | Existing first-pass changes; package smoke and full Python gate |
| 2 | 2: mailbox/schema/diagnostics | sequential-only | Task 1 package names; rollback/admission/history tests |
| 3 | 3: locks/wake/host settings | sequential-only | Task 2 active-connection write interface |
| 4 | 4: policy/remote/reports | sequential-only | Task 2 mailbox admission/write contracts; keep report protocol owner below diagnostics |
| 5 | 5: collaboration/UI | sequential-only | Task 2 pending/exposure seam and Task 4 policy direction |
| 6 | 6: CLI/test organization | sequential-only | Integrated feature owners from Tasks 2–5 |
| 7 | 7: relay/viewer/final acceptance | sequential-only | All Python names final; update embedded integration consumers once |

Parallel implementation permission: not requested. Review delegation was read-only under the code-reviewer skill. Tasks deliberately use sequential integration because package imports, facades, tests, and shared configuration overlap. Independent reviewers review each task's complete diff; no implementation task approves itself. Planner uses host-assigned Codex model/effort; a prompt cannot change those controls. Review agents inherited host controls; resolved model identities were not exposed.

Execution authorization now includes implementation and delegated sequential work. Every task after Task 1 additionally requires acceptance of the immediately preceding task, including its independent review. This sequencing rule applies even where the narrower interface dependency below names an earlier task. No commit, push, deployment, live user-setting mutation, or production data operation is authorized.

### Task 1: Establish package and runtime compatibility

**Goal / delivers:** F1; move each current module intact into the nearest final package owner before splitting bodies. Add `__main__.py`; keep `llm_bus:main` exporting the CLI entrypoint without eagerly loading optional collaboration runtime.

**Files / read first:** `pyproject.toml:project.scripts`, wheel packages, deptry and lint selectors; `tools/check_package.py:main`; `llm_bus_locks.py:schedule_check`; `llm_bus_collab_cli.py:_run_daemon`; `cloudflare/test/bridge.test.ts`; `README.md:Development`; `AGENTS.md:Repository workflow`. Update all Python imports and string-based imports/patch targets, including tests and embedded Python.

**Interfaces:** consume existing `main`, `Store`, `RemoteStore`, `Reports`, `CollabState` contracts; produce their package-qualified equivalents. Discover module names recursively from source, assert a nonempty inventory, import core names in isolated installed Python before installing `[collab]`, then import optional modules afterward. The discovery must include package initializers but avoid executing `__main__` as an ordinary import. Explicitly smoke-test `python -I -m llm_bus --help` separately.

**Intact-module move manifest:** move `llm_bus.py` to `cli/app.py`; store/schema/diagnostics to `mailbox/store.py`, `mailbox/schema.py`, `diagnostics/messages.py`; errors/routing to `errors.py`, `routing.py`; locks/dispatch/wake/install to `locks/service.py`, `wake/dispatch.py`, `wake/adapters.py`, `hosts/settings.py`; policy/remote/remote_store/reports to `policy/config.py`, `remote/bridge.py`, `remote/store.py`, `reports/store.py`; collab state/daemon/web/html to `collab/state.py`, `collab/daemon.py`, `collab/web/http.py`, `collab/web/layout.py`; remaining collab cli/identity/responder/transport to their same-named `collab/` modules. All paths are under `src/llm_bus/`. Create package initializers and entrypoint files. Preserve bodies in these temporary cohesive owners until their assigned decomposition task. Update qualified imports and patch targets immediately, including lazy strings and embedded relay scenarios. Later tasks may move symbols to the final destination map; update consumers in the same task.

**Execution / cleanup / review:** Wave 1, owned worktree, no predecessors. Record old-to-new module manifest before editing. Keep the single implementations; remove old flat files after consumers move. No legacy shim unless a supported external Python consumer is demonstrated. Review entrypoint, optional imports, monkeypatch location, wheel inventory and developer instructions.

- [ ] Establish pre-change checks: record 294 collected tests and existing core/optional import inventory.
- [ ] Characterize gap: current `glob('*.py')` cannot discover a nested package; add a meaningful package-check regression or exercise installed nested-module inventory directly.
- [ ] Move modules and update every identified consumer/configuration; no business-logic changes.
- [ ] Run `uv run poe package-check` and `uv run pytest`; expect all behavioral tests and installed entrypoints to pass.
- [ ] Run `sh tools/quality/check-static.sh`; expect every tool to exit 0.
- [ ] Review final references and removed paths independently; record results and any supported compatibility exception.

### Task 2: Decompose mailbox, schema and diagnostics

**Goal / delivers:** F2; use the destination map to separate storage owners while retaining a small `Store` facade.

**Files / read first:** current `Store.transaction`, `send`, `send_in_transaction`, `validate_reply`, `_pending_permitted`, `_pending_pages`, `history`, `acknowledge`; `ensure_schema`; diagnostics `message_status` and `synchronization_snapshot`. Own package mailbox/diagnostics modules and corresponding tests.

**Interfaces:** preserve `Store(path)`, `Store.read_only(path)`, `transaction()`, and the existing public methods/signatures. Extraction functions accept `sqlite3.Connection` where the facade currently owns a transaction. Keep body-free read-only status opening `mode=ro` without migration/chmod; history remains a separate body-bearing contract.

**Execution / cleanup / review:** Wave 2 after Task 1. Move record types once, migrate consumers, remove original method bodies as they delegate. Review bounded pending scans, current-policy admission, `BEGIN IMMEDIATE`, history cursor/scope/conversation semantics and migration rollback. No new query framework.

- [ ] Establish checks using mailbox, remote admission and lock rollback tests.
- [ ] Characterize unchanged transactions and schema versions; record direct callers of private patch targets.
- [ ] Extract by responsibility; keep one connection through each atomic operation.
- [ ] Run `uv run pytest tests`; expect unchanged test inventory and outcomes.
- [ ] Run `sh tools/quality/check-static.sh` and installed package check.
- [ ] Review SQL/transaction ownership and final references independently; record justified size exceptions.

### Task 3: Decompose locks, wake and host settings

**Goal / delivers:** F2; separate catalog, lifecycle, notices, timer and settings restoration using existing functions.

**Files / read first:** `Locks.acquire`, `release`, `_persist`, `_deliver`, `_hand_off`, `run_timer`; dispatch `_claim`, `_owns_lease`, `_finish`; `configure_hosts`, `_atomic_write`, `restore_bus_settings`; lock/wake/install tests.

**Interfaces:** keep transaction-passed mailbox writes from Task 2; wake adapter result shapes and dispatch signatures; timer module execution; host setting snapshot/restore and concurrent-edit protections. Extract settings restoration without changing target rechecks or restoration merge order.

**Execution / cleanup / review:** Wave 3 after Task 2. Own locks/wake/hosts modules and tests. Keep the already-small adapters intact. Remove moved SQL/notices/settings helper copies. Review persistence-before-wake, lease fencing, handoff fairness, crash recovery, and user-setting preservation.

- [ ] Establish existing lock, wake, rollback and installation portability checks.
- [ ] Characterize timer argv, atomic settings replace, lease and coverage boundaries.
- [ ] Extract owners; pass active transactions and existing state explicitly.
- [ ] Run `uv run pytest tests`; require all relevant concurrent/failure cases to pass.
- [ ] Run push-hook tools and installed package check.
- [ ] Independently review lookup/patch targets, timer entrypoint, and no duplicated helper bodies.

### Task 4: Decompose policy, remote synchronization and reports

**Goal / delivers:** F2; apply the remote/report destination map, keeping policy/protocol below transport orchestration.

**Files / read first:** `Config`, `load_config`, `receive_mode`, `read_token`; `RemoteStore.enqueue`, `admit_message`, `set_state`, scheduling methods; `Bridge._upload`, `_receive`, report sync methods; `Reports.enqueue`, `accepted`, `receive`. Own remote/policy/report modules, related diagnostics imports and remote tests.

**Interfaces:** preserve external wire fields, UUID canonicalization, transport state progression, report vocabulary and original recipient binding. Keep `Bridge` as one ordered sync-pass owner; extracted flows consume the same config snapshots, relay and persistence objects. Reload policy at existing final guards rather than moving every check to initial parsing.

**Execution / cleanup / review:** Wave 4 after Task 2. Move report validation out of broad configuration ownership, update every consumer once, remove obsolete implementations/re-exports without supported consumers. Review monitor isolation, policy revocation during publication/receipt/poll, replay/parent binding, batch membership validation and durable scheduling.

- [ ] Establish existing remote tests covering denied raw-relay peers, concurrent writers, reply ordering, report races and monitor isolation.
- [ ] Characterize per-pass cursor and transaction boundaries; record report and transport state owners separately.
- [ ] Extract SQL/protocol/workflows; keep final guard timing and single admission transaction.
- [ ] Run `uv run pytest tests`; expect no removed security/concurrency assertions.
- [ ] Run push-hook tools and installed package check; inspect import direction for cycles.
- [ ] Independently review every flow from CLI/bridge to policy and SQLite boundaries; record cleanup and size exceptions.

### Task 5: Decompose collaboration state, daemon and owner UI

**Goal / delivers:** F2/F3; separate codecs/policy/state operations, runtime workflows, and HTTP versus owner actions/rendering.

**Files / read first:** `CollabState.claim_invite`, revoke/message transition methods, `exposure_allowed`; daemon incoming/reply/responder completion paths; `OwnerPage`, `_Handler`, `serve_ui`; collab tests. Identify exact renamed method anchors from Task 1's manifest before editing.

**Interfaces:** retain `CollabState` facade while connection-taking pairing/peer/message functions move beneath it. Preserve transcript bytes, invite prefix/version, state paths, sender strings, asking-session binding, first-answer semantics, and optional Iroh lazy loading. One daemon owns transport, tasks, sending set, retries and shutdown.

**Execution / cleanup / review:** Wave 5 after Tasks 2/4. Update mailbox consumer to the explicit exposure owner. Keep HTTP token/Host/Origin/CSP and bounded body handling together. Keep escaping/script/hash together. Remove moved copies; no framework or independent transport instances.

- [ ] Establish invite, paired delivery, revoke, first-answer, responder restrictions and owner HTTP tests.
- [ ] Characterize atomic invite claims/revoke/transitions and post-response policy recheck.
- [ ] Extract state/workflow/UI owners; preserve caller-owned connections and daemon lifecycle.
- [ ] Run `uv run pytest tests`; require existing end-to-end paired-daemon and transport timeout tests.
- [ ] Run push-hook tools and isolated core/optional imports.
- [ ] Independently review monitor/text separation, immutable reply owner, live exposure checks and removed import strings.

### Task 6: Finish CLI and feature-test organization

**Goal / delivers:** F3; keep command parsing, command execution, host hooks, guidance and installation separate; organize all Python tests by behavior.

**Files / read first:** CLI `main`, `_parser`, `_human_command`, `_registered_command`, `_send`, `_hook`, `_guidance`, `_configure_host_hooks`; `tests/conftest.py`; all existing test files and fixture imports. Own CLI modules and the test folder move.

**Interfaces:** preserve commands/flags/help, JSON keys, identity-free status/history/monitor behavior, hook no-body notices and failure reporting after durable send. Root autouse fixture continues clearing host identity/socket/token environment. Scoped fixtures retain their original breadth; native wake prohibitions cannot disappear when test files move.

**Execution / cleanup / review:** Wave 6 after Tasks 2–5. Update monkeypatches at the actual consuming module, not a facade that no longer looks up the function. Keep test names/assertions; count parametrized cases before and after. Remove obsolete imports and local fixture copies after shared consumers move.

- [ ] Record `uv run pytest --collect-only -q` test IDs and fixture ownership before moves.
- [ ] Characterize command output, identity registration boundaries and fixture activation.
- [ ] Extract CLI workflows and move tests according to the ownership map.
- [ ] Compare collected tests by function/parameter identity, allowing only path changes; run full pytest.
- [ ] Run push-hook tools and package help checks.
- [ ] Independently review all command routes, fixture scopes and retained native wake guards; record any deliberate test addition/removal.

### Task 7: Relay/viewer cleanup and final acceptance

**Goal / delivers:** F3 and aggregate acceptance; finish the bounded relay/viewer splits and all integration consumers.

**Files / read first:** Worker router/auth/message/report/queue functions; D1 migrations; both relay test files and embedded Python imports/patches; viewer `parse_messages`, `fetch_history`, `Viewer`, `main`; plugin manifest; CI/quality configuration.

**Interfaces:** Worker default export, routes, D1/Queue bindings and existing schemas remain stable. Keep authorization inside SQL writes, current grant reads, duplicate-race handling and report compare-and-set. Viewer remains standalone Python 3.10+ with `python3 viewer.py` and no application-package import dependency.

**Execution / cleanup / review:** Wave 7 after all Python names stabilize. Own relay/viewer code and tests; preserve migrations/configuration unless an import/path consumer requires editing. Update any embedded scenario imports and native wake patch targets. Remove old flat implementations after final reachability checks; retain historical migrations/plans.

- [ ] Establish relay typecheck, runtime-backed tests/build, and viewer tests before extraction.
- [ ] Characterize route/binding/auth/SQL boundaries and executable plugin location.
- [ ] Extract a few cohesive relay/viewer owners; no artificial file-per-helper split.
- [ ] Run `uv run poe check`; expect every Python tool, test, build/install/import smoke check to pass. Run `bun install --frozen-lockfile`, `bun run check`, and `bun run test` in `cloudflare/`; test includes dry-run build, never deployment.
- [ ] Run `sh tools/quality/check-static.sh` explicitly for all push-hook tools. The actual pre-push wrapper also requires a clean committed HEAD; do not bypass that check or claim the wrapper ran on a dirty worktree.
- [ ] Independently review aggregate imports, transactions, permissions, entrypoints, test inventory, package inventory, old-file removal, and file-size exceptions. Record residual risks and verification evidence before completion.

## Review evidence, cleanup audit, and limitations

- Mode/authorization: read-only architectural audit plus this plan artifact. No application edits or removals in this review turn. Earlier first-pass changes remain uncommitted.
- Scope: inventory covered all 73 tracked repository files plus four new first-pass source files. Three read-only reviewers inspected core, collaboration, and remote/relay/viewer responsibility groups. Root review covered package/configuration, hooks, CI, tooling, instruction/document consumers and test structure.
- Trace coverage: CLI to mailbox/SQLite; locks to same-transaction notices and post-commit wake; pending scan to current policy to native adapters; remote upload/admission/receipt/report; collaboration pairing/revocation/reply ownership/exposure; owner HTTP guards; package loader and timer; embedded real relay integration consumers.
- Reachability: existing production operations are reachable through CLI, runtime, optional imports, plugin manifest, tests or persistence consumers. No production behavior is confirmed dead. Old flat implementations become cleanup candidates only after consumer migration; filenames alone do not justify deletion.
- Retained: historical SQL migrations, stored schemas/addresses, lazy optional modules, standalone viewer contract, test failure/concurrency guards and historical design plans. No externally documented Python import API was found in inspected runtime documentation; recheck before removing any disputed compatibility export.
- Tool evidence: clean-code MCP query for mixed state/policy/codec responsibility returned no strong pattern match. Recommendations derive from source ownership and traced contracts, not generic lint output. Earlier first-pass verification passed 294 tests and package checks; that is baseline evidence, not proof of this unimplemented plan.
- Limitations: this is a complete architectural inventory and principal-path assessment, not an exhaustive line-by-line correctness/security approval. Large test/document bodies were sampled at migration-sensitive anchors; lockfile payloads and generated outputs were not semantically audited. No remote deployment or cloud service was exercised.
- Self-review: requirements map to tasks; each task has an owner, inputs, concrete preserved interfaces, cleanup boundary and gate. Sequential waves avoid overlapping edits. Existing tests are reused; new coverage is justified specifically for nested package discovery/module execution. Final independent plan approval and implementation review remain separate work.
