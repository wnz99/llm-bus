"""Which llm-bus skill files apply to one coordination session.

The rule text lives in the skill's ``references/coordination/`` files; guidance names the files
that match the session's role, state and capabilities. Names are static constants, never built
from stored data.
"""

from __future__ import annotations

from dataclasses import dataclass

CORE = "core.md"
COORDINATOR = "coordinator.md"
WORKER = "worker.md"
GATES = "gates.md"
AUTO_MERGE = "auto-merge.md"
OWNER_DECISIONS = "owner-decisions.md"
REMOTE = "remote-coordination.md"
MEMORY_SNAPSHOT = "memory-snapshot.md"
RECOVERY = "recovery.md"
# Every file in the skill directory. `memory-snapshot.md` is named by `coordinator.md`, never by
# the selector.
ALL_GUIDANCE_FILES = (
    CORE,
    COORDINATOR,
    WORKER,
    GATES,
    AUTO_MERGE,
    OWNER_DECISIONS,
    REMOTE,
    MEMORY_SNAPSHOT,
    RECOVERY,
)
ACTIVE = "active"


@dataclass(frozen=True)
class RuleContext:
    """What one session's guidance needs to pick its skill files."""

    role: str  # "coordinator" | "worker"
    state: str  # "active" | "suspended" | "expired" | "revoked" | "out_of_scope"
    gates: frozenset[str]  # gates in effect: project delegation (coordinator) or binding (worker)
    auto_merge: bool
    owner_decisions: bool  # owner decisions or decision defaults delegated
    remote_coordinator: bool  # "/" in the project's coordinator address
    remote_peers: bool  # the folder's repository config lists at least one peer


def select(context: RuleContext, *, recovery: bool) -> tuple[str, ...]:
    """Files to load, in order; an unknown state selects like ``suspended``."""
    if context.state != ACTIVE:
        return (CORE, RECOVERY)
    coordinator = context.role == "coordinator"
    wanted = (
        (COORDINATOR if coordinator else WORKER, True),
        (GATES, bool(context.gates)),
        (AUTO_MERGE, context.auto_merge),
        (OWNER_DECISIONS, context.owner_decisions),
        (REMOTE, context.remote_peers if coordinator else context.remote_coordinator),
        (RECOVERY, recovery),
    )
    return (CORE, *(name for name, applies in wanted if applies))
