"""Lifecycle ownership."""

from __future__ import annotations

TERMINAL = ("completed", "failed")


TRANSITION_CONFLICT = "Report pending or lifecycle transition conflicts"


def transition_conflicts(prior_status: str | None, status: str) -> bool:
    """A changed report may not follow a terminal one or return to received."""
    if prior_status is None:
        return False
    return prior_status in TERMINAL or status == "received"
