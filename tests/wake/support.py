"""wake test support."""

from pathlib import Path

import llm_bus.wake.adapters
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store


def requested(*_args: object, **_kwargs: object) -> llm_bus.wake.adapters.WakeResult:
    return {"status": "requested", "via": "test"}


def mailbox(tmp_path: Path) -> tuple[Store, str, SenderContext]:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("codex", "recipient", str(tmp_path))
    sender = SenderContext("claude", "sender", str(tmp_path))
    store.send(sender, recipient, "first")
    return store, recipient, sender
