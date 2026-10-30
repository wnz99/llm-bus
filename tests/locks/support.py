"""locks test support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm_bus.mailbox.store import Store

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def no_timer(_key: object) -> None:
    """Tests drive checks directly instead of spawning detached timers."""


def record_wakes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    woken: list[str] = []

    def fake_wake(recipient: str, *_args: object, **_kwargs: object) -> dict[str, str]:
        woken.append(recipient)
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", fake_wake)
    return woken


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "bus.sqlite3")


def bodies(store: Store, recipient: str) -> list[str]:
    return [message["body"] for message in store.inbox(recipient)]


def overdue(store: Store) -> None:
    with store.transaction() as db:
        db.execute("UPDATE resource_locks SET check_at='2000-01-01T00:00:00.000Z'")


def holder_store(tmp_path: Path) -> tuple[Store, str]:
    mailbox = make_store(tmp_path)
    return mailbox, mailbox.register("claude", "one", "/project")
