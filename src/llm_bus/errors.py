"""Errors shared by mailbox operations and retained diagnostics."""


class BusError(ValueError):
    """Invalid operation at the local bus boundary."""
