"""collab isolation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from llm_bus.collab import identity

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True, name="isolated_collab")
def isolated_collab_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "collab"
    monkeypatch.setenv("LLM_BUS_COLLAB_HOME", str(home))
    monkeypatch.setenv("LLM_BUS_COLLAB_LOCAL_ONLY", "1")
    monkeypatch.setenv(identity.TEST_KEYS_ENV, str(tmp_path / "keys"))
    (tmp_path / "keys").mkdir()
    return home
