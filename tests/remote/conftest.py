"""remote isolation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BUS_CONFIG", str(tmp_path / "config.toml"))

    def requested(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", requested)
