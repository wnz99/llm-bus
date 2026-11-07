"""Coordination fixtures never submit native host wakes."""

import pytest

from llm_bus.coordination import cli


@pytest.fixture(autouse=True)
def isolated_join_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_host_wake(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "unsupported"}

    monkeypatch.setattr(cli, "dispatch_pending", no_host_wake)
