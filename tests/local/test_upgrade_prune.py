"""Confined deletion of old packages and releases."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from llm_bus.cli.upgrade import prune_entries
from llm_bus.errors import BusError

if TYPE_CHECKING:
    from pathlib import Path


def _tree(base: Path) -> Path:
    (base / "backups").mkdir(parents=True)
    (base / "backups" / "file").write_text("x")
    root = base / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "b" / "x").write_text("x")
    (root / "c.txt").write_text("x")
    (root / "keep").mkdir()
    return root


def test_removes_everything_except_keep(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert prune_entries(root, keep="keep") == ["a", "b", "c.txt"]
    assert [entry.name for entry in root.iterdir()] == ["keep"]
    assert (tmp_path / "backups" / "file").exists()


def test_symlink_entry_is_unlinked_not_followed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_text("x")
    (root / "link").symlink_to(outside, target_is_directory=True)
    assert "link" in prune_entries(root)
    assert (outside / "file").exists()
    assert list(root.iterdir()) == []


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    real = _tree(tmp_path)
    link = tmp_path / "linked-root"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(BusError, match="not a real directory"):
        prune_entries(link)
    assert (real / "c.txt").exists()


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    assert prune_entries(tmp_path / "absent") == []


def test_unsafe_rmtree_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path)
    monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False)  # pylint: disable=clean-code-boolean-flag-argument
    with pytest.raises(BusError, match="rmtree follows symlinks"):
        prune_entries(root)
    assert (root / "c.txt").exists()


def test_partial_failure_reports_removed_and_keeps_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
    (root / "c.txt").write_text("x")
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path: str) -> None:
        if str(path).endswith("/b"):
            raise OSError("denied")
        real_rmtree(path)

    # Without this flag the platform check would refuse before any removal.
    monkeypatch.setattr(flaky_rmtree, "avoids_symlink_attacks", True, raising=False)  # pylint: disable=clean-code-boolean-flag-argument
    monkeypatch.setattr("llm_bus.cli.upgrade.shutil.rmtree", flaky_rmtree)
    with pytest.raises(BusError, match=r"stopped at b; removed \['a'\]"):
        prune_entries(root)
    assert not (root / "a").exists()
    assert (root / "b").is_dir()
    assert (root / "c.txt").exists()
