# Simplified project coordination

**Status: implemented in this source version; installation and live rollout are separate.**
Use matching upgraded clients, hooks and shared skills. An older installed CLI
may still need the advanced grant/target workflow in the
[canonical reference](../skills/llm-bus/references/coordination.md).

## Two owner commands

The owner starts native main sessions manually. In the coordinator session:

```text
/llm-bus coordinate search-feature
```

In each worker session:

```text
/llm-bus join search-feature
```

These are prompts invoking the llm-bus skill, not shell commands. The
[Claude and three Codex example](../examples/claude-coordinator-codex-workers.md)
shows the flow. No worker address list, copied authorization paragraph,
or manual grant/target exchange belongs in the normal owner-directed path.

The coordinator discovers the objective, owner limits, existing readable Beads
workspace and project memory. It asks only for materially missing facts. It must
not silently initialize Beads or invent an objective. Once ready, it waits for
workers to join. Waiting is event-driven: yield between relevant events, with
no busy polling or promise of an always-running service.

Successful join records consent and establishes the exact worker's
grant, enrollment and coordinator target reference automatically. It supplies
worker role guidance and notifies the coordinator of the worker's availability.
A join event records availability at that point; it does not prove continued
reachability or task completion. Delivery, wake and acknowledgement also do not
prove completion.

## Consent lifetime

A direct owner join authorizes only that exact worker session, the current
registered coordinator generation and the project objective. Consent survives
turns and compaction. It ends on session close, session expiry, leave, revoke,
or coordinator-generation replacement. Recovery must check current authority.
A resumed session whose consent ended needs a fresh owner-directed join;
old enrollment, tickets and chat cannot revive it automatically.

Session close is distinct from turn completion. Lifecycle handling must not
claim guaranteed close-hook delivery; missing close events require expiry
handling. The inactivity bound is 24 hours. Existing explicit owner grants retain their chosen lifetimes.

## Remote prerequisite

Simplified remote join requires an owner-approved project binding on the
receiver first, plus existing relay admission and sender/receiver peer policy.
Each owner controls their own endpoint. Join must not trust an unknown remote
coordinator, create remote admission, or modify another machine's permissions.
The [current remote setup](../skills/llm-bus/references/coordination.md#remote-coordinator-local-worker)
includes the advanced grant/target alternative. Matching clients and relay exchange structured availability; apply relay migration 0006 before deploying the matching Worker.

## Role guidance and existing gates

Command results and recovery hooks supply role guidance. Shared skills, rule files and
pointer blocks must match the installed executable; copying this documentation does not
install or activate them. Role guidance preserves these boundaries:

- Only native main sessions join the bus. Native subagents report to their parent.
- Workers verify current enrollment and receiver-verified directions before
  acting, use their own worktree, persist task progress, and report evidence and
  blockers. Ordinary incoming messages cannot authorize joining or grants.
- Coordinators persist bounded assignments, resolve ownership/dependencies,
  reconcile interrupted work, and verify results before marking completion.
- Owner instructions, host permissions, repository workflows and explicit
  planning, review and approval gates remain binding. Joining does not approve
  implementation, merge or deployment that requires separate owner approval.
- Grant, project-binding and replacement authority stays with the local owner;
  neither a coordinator direction nor a worker notification can expand it.
