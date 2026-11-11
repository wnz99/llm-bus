# Resource lock instruction examples

Locks are acquired by agents following instructions. The bus does not detect
database access, file writes, or deployments and acquire locks automatically.
It records ownership, queues waiters, and sends reminders. All participants must
cooperate; a lock does not prevent another process from accessing the resource.
Locks coordinate sessions on one machine, not remote peers.

The general lock procedure (list, acquire, wait, renew, release) is part of the
[llm-bus rules file](instructions/llm-bus.md), installed as described in
[Install agent instructions](agent-instructions.md). Check
`llm-bus --format agent lock --help` for the installed syntax.

## Copyable project lock names

Optionally add a project-specific name table so every session uses the same
lock name and scope for the same resource. Paste it into the project's
`AGENTS.md`, below the llm-bus pointer block, and into every `CLAUDE.md` that
holds the pointer block (see the project row in
[Install agent instructions](agent-instructions.md#2-copy-the-rule-files)). Replace the example rows with the project's
actual shared resources. The table is a naming convention, not executable
configuration or a grant of access.

```md
## Shared resource lock names

Before an operation below, follow the lock steps in the llm-bus rules using this exact lock name and scope.

| Operation | Lock name | Scope |
| --- | --- | --- |
| Migrate or reset the local shared Orders development database | db:orders-dev | Repository |
| Regenerate the schema snapshot shared by sessions | file:schema-snapshot | Repository |
| Deploy to the staging target used by multiple local repositories | deploy:staging | Machine (`--shared`) |
```

## Two-agent walkthrough

Both agents use the same local bus database and repository scope. Agent A starts
the database operation only after this returns `acquired`:

```sh
llm-bus --format agent lock list
llm-bus --format agent lock acquire db:orders-dev --check 45m \
  --describe "Local shared Orders development database" \
  --reason "Apply development migrations"
```

Agent B requests the same name. This returns `waiting` with exit status 3,
without blocking the command, so `acquire NAME && command` never runs the
command unlocked. If B later no longer needs it, `lock cancel db:orders-dev`
leaves the queue; an earlier `--notify` request stays in A's inbox. `--notify` sends A a request and attempts a wake; omit it to queue quietly.
Repeated notifications are limited to once per five minutes per waiter.

```sh
llm-bus --format agent lock acquire db:orders-dev --notify --reason "Reset development fixtures"
```

If A's host process is killed, the next lock command by any session finds
that the process has exited. The bus then ends A's session and grants the
lock to B. If A's process still runs but A is stuck, the owner can run
`llm-bus lock release db:orders-dev --force --reason "A is stuck"` from an
interactive terminal outside any agent session. A project coordinator can run
the same command from its session.

After 45 minutes, A receives a check asking whether it can release. This is a
reminder, not automatic release. If A still needs the database, it renews:

```sh
llm-bus --format agent lock acquire db:orders-dev --check 30m
```

When finished, A releases:

```sh
llm-bus --format agent lock release db:orders-dev
```

Ownership passes to the earliest live waiter. B receives a grant message with
a wake attempt. A requested wake does not prove the agent handled the message.
B confirms ownership and sets its estimate before operating:

```sh
llm-bus --format agent lock status db:orders-dev
llm-bus --format agent lock acquire db:orders-dev --check 20m
```

B releases the same lock when finished.

## Naming and scope

Names identify reservations, not the underlying database or file. For example,
`db:orders-dev` and `orders-local` can refer to the same database in human terms
but create separate locks. Similar-name checks catch some aliases; they cannot
infer every resource's identity. Agree on the mapping above and reuse it.

Repository scope lets linked worktrees coordinate. Machine scope requires
`--shared` on each applicable command, for example:

```sh
llm-bus --format agent lock acquire deploy:staging --shared --check 15m \
  --describe "Staging deployment target shared by local repositories"
llm-bus --format agent lock status deploy:staging --shared
llm-bus --format agent lock release deploy:staging --shared
```

## Reminder and expiry behavior

`--check 45m` schedules a reminder. `--ttl 1440` sets a lease of 1,440 minutes
(24 hours), the default; the maximum lease is seven days. The check estimate
must fall within the lease. Renewal resets both deadlines from the renewal time.
One reminder is sent per estimate; unanswered checks do not repeatedly wake the
holder without renewal.

A detached timer attempts the scheduled check. If it is lost, a later lock
command or hook processes the overdue check. Lock commands and hooks also
process expired leases and sessions marked ended, handing their locks to live
waiters. Do not assume handoff happens at the exact expiry instant.

Expiry does not stop the old holder's process. For resources requiring enforced
exclusion, use resource-level controls as well; advisory bus ownership alone
cannot prevent concurrent access.
