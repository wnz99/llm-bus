"""Upgrade command sequence with a real Git checkout and a fake uv."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast, final

import pytest

from llm_bus.cli.app import main

if TYPE_CHECKING:
    from collections.abc import Callable

WHEEL = "llm_bus-0.1.0-py3-none-any.whl"
UV = "/fake/uv"
real_run = subprocess.run
GIT = cast("str", shutil.which("git"))


def _fake_which(name: str) -> str | None:
    return GIT if name == "git" else UV


def _git(repo: Path, *args: str) -> str:
    return real_run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_checkout(repo: Path, name: str = "llm-bus") -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.invalid")
    (repo / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    (repo / ".python-version").write_text("3.12\n")
    (repo / "tracked.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")


@final
class FakeBus:  # pylint: disable=too-many-instance-attributes
    """Home, checkout, uv tool dir and a recording subprocess fake."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = root / "home"
        self.packages = self.home / ".local/share/llm-bus/packages"
        self.releases = self.home / ".local/state/llm-bus/releases"
        self.state = self.home / ".local/state/llm-bus"
        self.tools = root / "tools"
        self.receipt = self.tools / "llm-bus/uv-receipt.toml"
        self.binary = self.home / ".local/bin/llm-bus"
        self.repo = root / "checkout"
        self.calls: list[list[str]] = []
        self.binary_rc = 0
        self.python_rc = 0
        self.install_fault: str | None = None
        self.built_sources: list[list[str]] = []
        _make_checkout(self.repo)
        self.package_id = _git(self.repo, "rev-parse", "HEAD")[:8]
        self.previous = self.packages / "fabcb404" / WHEEL
        for path in (self.previous, self.binary, self.state / "backups/keep"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        for name in ("old1", ".upgrade-old"):
            (self.packages / name).mkdir()
        (self.releases / "r1").mkdir(parents=True)
        (self.state / "deploy-backups").mkdir()
        (self.state / "deploy-backups/keep").write_text("x")
        self.write_receipt(self.previous, ["collab"], "/prev/python")
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.chdir(self.repo)
        monkeypatch.setattr("llm_bus.cli.upgrade.shutil.which", _fake_which)
        monkeypatch.setattr("llm_bus.cli.upgrade.subprocess.run", self.run)

    def write_receipt(self, wheel: Path, extras: list[str], python: str) -> None:
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        self.receipt.write_text(
            "[tool]\n"
            f'requirements = [{{ name = "llm-bus", extras = {json.dumps(extras)}, '
            f'path = "{wheel}" }}]\n'
            f'python = "{python}"\n'
            f'entrypoints = [{{ name = "llm-bus", install-path = "{self.binary}", '
            'from = "llm-bus" }]\n'
        )

    def run(
        self, argv: list[str], *, env: dict[str, str] | None = None, **_options: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[0] == GIT:
            return real_run(argv, capture_output=True, text=True, check=False, env=env)
        stdout = ""
        if argv[0] == UV and argv[1:3] == ["tool", "dir"]:
            stdout = str(self.tools)
        elif argv[0] == UV and argv[1] == "build":
            out = Path(argv[argv.index("--out-dir") + 1])
            out.mkdir(parents=True)
            (out / WHEEL).write_text("built")
            self.built_sources.append(sorted(p.name for p in Path(argv[-1]).iterdir()))
        elif argv[0] == UV and argv[1:3] == ["tool", "install"]:
            return self._install(argv)
        elif argv[0] == str(self.tools / "llm-bus/bin/python"):
            return subprocess.CompletedProcess(argv, self.python_rc, "", "no module")
        elif argv[0] == str(self.binary):
            stdout = "usage: llm-bus upgrade"
            return subprocess.CompletedProcess(argv, self.binary_rc, stdout, "boom")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    def _install(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Apply the one-shot install_fault to the next install; later installs succeed."""
        fault, self.install_fault = self.install_fault, None
        if fault == "fail":
            return subprocess.CompletedProcess(argv, 1, "", "install boom")
        path, _, extras = argv[-1].partition("[")
        if fault == "receipt":
            path = str(Path(path).with_name("other.whl"))
        python = argv[argv.index("--python") + 1] if "--python" in argv else ""
        self.write_receipt(Path(path), [e for e in extras.rstrip("]").split(",") if e], python)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def commands(self, *words: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == UV and c[1 : 1 + len(words)] == list(words)]

    def entries(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.home))
            for root in (self.packages, self.releases)
            for p in root.rglob("*")
        )


@pytest.fixture(name="bus")
def bus_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBus:
    return FakeBus(tmp_path, monkeypatch)


def _run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(["upgrade"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _assert_untouched(bus: FakeBus, before: list[str]) -> None:
    assert bus.entries() == before
    assert (bus.state / "backups/keep").exists()
    assert (bus.state / "deploy-backups/keep").exists()


def test_success_installs_head_and_prunes_only_owned_roots(
    bus: FakeBus, capsys: pytest.CaptureFixture[str]
) -> None:
    (bus.repo / "scratch.txt").write_text("untracked")
    code, out, _err = _run(capsys)
    result = cast("dict[str, object]", json.loads(out))
    wheel = bus.packages / bus.package_id / WHEEL
    assert code == 0
    assert result["package_id"] == bus.package_id
    assert result["reinstalled"] is True
    assert result["wheel"] == str(wheel)
    assert bus.commands("tool", "install")[0][3:] == [
        "--force",
        "--python",
        "3.12",
        f"{wheel}[collab]",
    ]
    assert "scratch.txt" not in bus.built_sources[0]
    assert "tracked.txt" in bus.built_sources[0]
    assert [p.name for p in bus.packages.iterdir()] == [bus.package_id]
    assert list(bus.releases.iterdir()) == []
    assert any(c[0] == GIT and c[-2:] == ["worktree", "prune"] for c in bus.calls)
    assert (bus.state / "backups/keep").exists()
    assert (bus.state / "deploy-backups/keep").exists()
    next_step = str(result["next_step"])
    assert "llm-bus install" in next_step
    assert "llm-bus bridge" in next_step


def _binary_fails(bus: FakeBus) -> None:
    bus.binary_rc = 1


def _collab_import_fails(bus: FakeBus) -> None:
    bus.python_rc = 1


def _receipt_points_elsewhere(bus: FakeBus) -> None:
    bus.install_fault = "receipt"


def _install_fails(bus: FakeBus) -> None:
    bus.install_fault = "fail"


@pytest.mark.parametrize(
    "inject_fault",
    [_binary_fails, _collab_import_fails, _receipt_points_elsewhere, _install_fails],
)
def test_install_or_verification_failure_deletes_nothing_and_restores_previous(
    bus: FakeBus, capsys: pytest.CaptureFixture[str], inject_fault: Callable[[FakeBus], None]
) -> None:
    inject_fault(bus)
    code, out, err = _run(capsys)
    installs = bus.commands("tool", "install")
    assert code == 1
    assert out == ""
    assert "nothing deleted" in err
    assert "restored" in err
    assert installs[1][3:] == ["--force", "--python", "/prev/python", f"{bus.previous}[collab]"]
    assert {"old1", ".upgrade-old", "fabcb404", bus.package_id} <= {
        p.name for p in bus.packages.iterdir()
    }
    assert (bus.releases / "r1").exists()
    assert (bus.state / "backups/keep").exists()


def test_prune_failure_reports_packages_already_removed(
    bus: FakeBus, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(bus.releases)
    bus.releases.symlink_to(bus.home)
    code, out, err = _run(capsys)
    assert code == 1
    assert out == ""
    assert "Refusing to prune" in err
    assert "removed packages ['.upgrade-old', 'fabcb404', 'old1']" in err
    assert [p.name for p in bus.packages.iterdir()] == [bus.package_id]


def _point_at_head(bus: FakeBus) -> Path:
    wheel = bus.packages / bus.package_id / WHEEL
    wheel.parent.mkdir()
    wheel.write_text("active")
    bus.write_receipt(wheel, ["collab"], "/prev/python")
    return wheel


def test_same_head_rerun_verifies_without_build_or_install(
    bus: FakeBus, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = _point_at_head(bus)
    code, out, _err = _run(capsys)
    assert code == 0
    assert json.loads(out)["reinstalled"] is False
    assert not bus.commands("build")
    assert not bus.commands("tool", "install")
    assert wheel.read_text() == "active"
    assert [p.name for p in bus.packages.iterdir()] == [bus.package_id]


def test_same_head_verification_failure_deletes_nothing(
    bus: FakeBus, capsys: pytest.CaptureFixture[str]
) -> None:
    _point_at_head(bus)
    before = bus.entries()
    bus.binary_rc = 1
    code, _out, err = _run(capsys)
    assert code == 1
    assert "nothing deleted" in err
    assert not bus.commands("tool", "install")
    _assert_untouched(bus, before)


def _remove_receipt(bus: FakeBus) -> None:
    bus.receipt.unlink()


def _directory_receipt(bus: FakeBus) -> None:
    bus.receipt.write_text(
        f'[tool]\nrequirements = [{{ name = "llm-bus", directory = "{bus.repo}" }}]\n'
    )


def _deleted_wheel(bus: FakeBus) -> None:
    bus.previous.unlink()


@pytest.mark.parametrize("break_previous", [_remove_receipt, _directory_receipt, _deleted_wheel])
def test_unrestorable_previous_refused_before_build(
    bus: FakeBus, capsys: pytest.CaptureFixture[str], break_previous: Callable[[FakeBus], None]
) -> None:
    break_previous(bus)
    before = bus.entries()
    code, _out, err = _run(capsys)
    assert code == 1
    assert "cannot be restored" in err
    assert not bus.commands("build")
    assert not bus.commands("tool", "install")
    _assert_untouched(bus, before)


def test_dirty_tracked_checkout_refused_before_build(
    bus: FakeBus, capsys: pytest.CaptureFixture[str]
) -> None:
    (bus.repo / "tracked.txt").write_text("changed\n")
    code, _out, err = _run(capsys)
    assert code == 1
    assert "commit or stash" in err
    assert not bus.commands("build")


def test_non_llm_bus_checkout_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bus = FakeBus(tmp_path, monkeypatch)
    (bus.repo / "pyproject.toml").write_text('[project]\nname = "other"\n')
    code, _out, err = _run(capsys)
    assert code == 1
    assert "not an llm-bus checkout" in err
    assert not bus.commands("build")
    assert not bus.commands("tool")


@pytest.mark.parametrize("root_name", ["packages", "releases"])
def test_checkout_inside_prune_root_refused(
    bus: FakeBus,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    root_name: str,
) -> None:
    inside = (bus.packages if root_name == "packages" else bus.releases) / "wt"
    _make_checkout(inside)
    monkeypatch.chdir(inside)
    before = bus.entries()
    code, _out, err = _run(capsys)
    assert code == 1
    assert "which the upgrade prunes" in err
    assert not bus.commands("build")
    assert not bus.commands("tool", "install")
    _assert_untouched(bus, before)


def test_unsafe_rmtree_refused_before_build(
    bus: FakeBus, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil.rmtree, "avoids_symlink_attacks", False)  # pylint: disable=clean-code-boolean-flag-argument
    before = bus.entries()
    code, _out, err = _run(capsys)
    assert code == 1
    assert "rmtree follows symlinks" in err
    assert not bus.commands("build")
    assert not bus.commands("tool", "install")
    _assert_untouched(bus, before)
