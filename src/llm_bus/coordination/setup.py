"""Guided coordinator setup: agent profile and terminal confirmation wizard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from llm_bus.coordination import cli, delegation
from llm_bus.errors import BusError

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_bus.mailbox.store import Store

ORIGIN_PREFIX = "origin/"
BRANCH_HINT = (
    "Detected from origin/HEAD; check the repository instructions for a different "
    "integration branch."
)
ORIGIN_HEAD_TIMEOUT = 10
YES = "yes"
INSTRUCTIONS_STEP = (
    "Optional: set owner standing instructions with llm-bus coordination instructions "
    "PROJECT --audience coordinator|worker --file PATH"
)


def detect_integration_branch(cwd: str) -> tuple[str | None, str]:
    """Read the local origin/HEAD symbolic ref; never contacts the network."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed git arguments
            [  # noqa: S607
                "git",
                "-C",
                cwd,
                "symbolic-ref",
                "--quiet",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=ORIGIN_HEAD_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "undetected"
    name = done.stdout.strip()
    if done.returncode != 0 or not name.startswith(ORIGIN_PREFIX):
        return None, "undetected"
    return name.removeprefix(ORIGIN_PREFIX), "origin-head"


def _items(branch: str | None) -> list[str]:
    return [] if branch is None else [f"{gate}:{branch}" for gate in delegation.GATES]


def profile(cwd: str) -> dict[str, object]:
    """Default setup for an agent to confirm with the owner; stores nothing."""
    branch, source = detect_integration_branch(cwd)
    flags = [] if branch is None else [delegation.AUTO_MERGE]
    return {
        "delegate": _items(branch),
        "flags": flags,
        "integration_branch": {"name": branch, "source": source},
        "floor": list(delegation.FLOOR),
        "steps": [
            (
                "Check the repository instructions for an integration branch that overrides "
                "the detected one."
            ),
            (
                "Ask the owner through the native question UI: objective, Beads workspace, "
                "memory ticket, integration branch, each delegated gate, and each flag "
                "(auto-merge needs a merge or merge-admin gate; the coordinator still accepts "
                "every gate record)."
            ),
            "The owner may disable items or change target branches, never add floor items.",
            "Run only the confirmed flags with the command below.",
            INSTRUCTIONS_STEP,
        ],
        "command": (
            "llm-bus --format agent coordinate PROJECT --objective OBJECTIVE "
            "--beads-workspace PATH --memory-ticket TICKET --delegate GATE:BRANCH ..."
            + (" --auto-merge" if flags else "")
        ),
    }


def _gate_enabled(answer: str) -> bool:
    """Default profile is the fullest delegation: Enter keeps the gate enabled."""
    return answer.strip().lower() in {"", "y", YES}


def _coordinator(store: Store, cwd: str, identity: str | None, ask: Callable[[str], str]) -> str:
    if identity is not None:
        answer = ask(f"Coordinator [{identity}]: ").strip()
        return answer or identity
    choices = [agent["address"] for agent in store.agents(cwd)]
    if not choices:
        raise BusError("No registered sessions in this folder; run coordinate inside the agent")
    listing = "\n".join(f"  {number}. {address}" for number, address in enumerate(choices, 1))
    answer = ask(f"Coordinator session:\n{listing}\nNumber: ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
        raise BusError("Pick a listed coordinator number")
    return choices[int(answer) - 1]


def _cancellable(ask: Callable[[str], str]) -> Callable[[str], str]:
    """End of input or Ctrl-C at any prompt is a clean error; nothing is stored."""

    def guarded(prompt: str) -> str:
        try:
            return ask(prompt)
        except (EOFError, KeyboardInterrupt) as error:
            raise BusError("Setup cancelled; nothing stored") from error

    return guarded


def run_wizard(  # noqa: PLR0913 - explicit inputs
    store: Store,
    label: str,
    cwd: str,
    identity: str | None,
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> dict[str, object] | None:
    """Ask, show a review screen and store only after an explicit yes."""
    # pylint: disable=too-many-locals,too-many-arguments
    label = cli.new_project_label(store, cwd, label)
    detected, _source = detect_integration_branch(cwd)
    workspace_default = cwd if (Path(cwd) / ".beads").exists() else ""
    ask = _cancellable(ask)
    objective = cli.text_reference(ask("Objective: ").strip())
    workspace = cli.beads_workspace(
        ask(f"Beads workspace [{workspace_default}]: ").strip() or workspace_default
    )
    ticket = cli.text_reference(ask("Memory ticket: ").strip())
    branch = ask(f"Integration branch [{detected or ''}]: ").strip() or detected or ""
    specs = (
        [
            f"{gate}:{branch}"
            for gate in delegation.GATES
            if _gate_enabled(ask(f"Delegate {gate}? [yes]/no: "))
        ]
        if branch
        else []
    )
    merge_chosen = any(spec.split(":", 1)[0] in delegation.MERGE_GATES for spec in specs)
    auto_merge = merge_chosen and _gate_enabled(ask("Delegate auto-merge? [yes]/no: "))
    # Existing scripted answers end at the coordinator prompt, so the owner is asked about
    # owner decisions only when no gate remains; otherwise decisions stay off in the wizard.
    owner_decisions = not specs and _gate_enabled(ask("Delegate owner decisions? [yes]/no: "))
    chosen = (
        delegation.Delegation.from_specs(
            specs, auto_merge=auto_merge, owner_decisions=owner_decisions
        )
        if specs or owner_decisions
        else None
    )
    coordinator = _coordinator(store, cwd, identity, ask)
    args: dict[str, object] = {
        "objective": objective,
        "beads_workspace": workspace,
        "memory_ticket": ticket,
        "delegate": specs,
        "auto_merge": auto_merge,
        "owner_decisions": owner_decisions,
    }
    _review(say, label, coordinator, args, chosen)
    if ask("Store this setup? (type yes): ").strip().lower() != YES:
        say("Nothing stored.")
        return None
    created = cli.create_for(store, cwd, label, args, coordinator, current=identity)
    say(INSTRUCTIONS_STEP)
    return created


def _review(
    say: Callable[[str], None],
    label: str,
    coordinator: str,
    args: dict[str, object],
    chosen: delegation.Delegation | None,
) -> None:
    say(f"Project: {label}")
    say(f"Coordinator: {coordinator}")
    say(f"Objective: {args['objective']}")
    say(f"Beads workspace: {args['beads_workspace']}")
    say(f"Memory ticket: {args['memory_ticket']}")
    say(BRANCH_HINT)
    say("Delegations: " + (", ".join(chosen.items()) if chosen else "none"))
    if chosen is not None and chosen.targets("merge-admin"):
        targets = ", ".join(chosen.targets("merge-admin"))
        say(f"merge-admin bypasses only the required-approvals rule on: {targets}")
    if chosen is not None and chosen.auto_merge:
        say(
            "auto-merge: each merge still needs the coordinator to accept the worker's gate "
            "record, which sends the merge approval; the worker checks CI before merging."
        )
    say("Never delegated (floor): " + ", ".join(delegation.FLOOR))
