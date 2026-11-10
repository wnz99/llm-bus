"""Guided coordinator setup: agent profile, wizard confirmation and storage."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import main
from llm_bus.coordination.setup import detect_integration_branch, profile, run_wizard
from llm_bus.errors import BusError
from llm_bus.mailbox.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

GATES = ("commit", "push", "merge", "merge-admin")
HINT = "Detected from origin/HEAD; check the repository instructions"


def git(folder: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(folder), *argv], check=True, capture_output=True)  # noqa: S607


@pytest.fixture(name="repo")
def make_repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(
        path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@x",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    git(path, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    (path / ".beads").mkdir()
    return path


def scripted(replies: list[str]) -> tuple[Callable[[str], str], list[str]]:
    prompts: list[str] = []
    queue = iter(replies)

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(queue)

    return ask, prompts


def counts(store: Store) -> tuple[int, int, int]:
    with store.transaction() as db:
        return cast(
            "tuple[int, int, int]",
            db.execute(
                "SELECT (SELECT COUNT(*) FROM coordination_projects),"
                "(SELECT COUNT(*) FROM coordination_delegations),"
                "(SELECT COUNT(*) FROM agents)"
            ).fetchone(),
        )


def answers(
    repo: Path, *, gates: tuple[str, ...] = ("yes",) * 4, confirm: str = "yes"
) -> list[str]:
    return ["Ship it", str(repo), "bus-1", "", *gates, "", confirm]


def test_detects_origin_head_without_network(repo: Path, tmp_path: Path) -> None:
    assert detect_integration_branch(str(repo)) == ("develop", "origin-head")
    assert detect_integration_branch(str(tmp_path)) == (None, "undetected")


def test_noninteractive_setup_prints_profile_and_stores_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(db))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "owner")
    monkeypatch.chdir(repo)
    assert main(["--format", "agent", "coordinate", "demo", "--setup"]) == 0
    out = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert out["delegate"] == [f"{gate}:develop" for gate in GATES]
    assert out["floor"]
    assert out["steps"]
    assert "llm-bus" in str(out["command"])
    assert profile(str(repo)) == out
    assert counts(Store(db)) == (0, 0, 0)


def test_setup_needs_no_host_identity_and_registers_no_session(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.chdir(repo)
    assert main(["coordinate", "demo", "--setup"]) == 0


def test_profile_without_origin_head_delegates_nothing(tmp_path: Path) -> None:
    out = profile(str(tmp_path))
    assert out["delegate"] == []
    assert out["integration_branch"] == {"name": None, "source": "undetected"}


def test_wizard_stores_only_after_yes(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    before = counts(store)
    ask, _ = scripted(answers(repo, confirm="no"))
    said: list[str] = []
    assert run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=said.append) is None
    assert counts(store) == before
    assert any(HINT in line for line in said)
    ask, _ = scripted(answers(repo))
    said.clear()
    result = run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=said.append)
    assert result is not None
    assert result["enrolled"] is True
    assert counts(store) == (1, 1, before[2])
    screen = "\n".join(said)
    for item in (f"{gate}:develop" for gate in GATES):
        assert item in screen
    assert "merge-admin bypasses only the required-approvals rule" in screen
    assert "force-push" in screen
    assert HINT in screen


def test_wizard_disabled_gate_is_omitted(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    ask, _ = scripted(answers(repo, gates=("yes", "yes", "yes", "no")))
    said: list[str] = []
    assert run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=said.append)
    screen = "\n".join(said)
    assert "merge-admin:develop" not in screen
    assert "bypasses" not in screen
    with store.transaction() as db:
        (text,) = cast(
            "tuple[str]",
            db.execute("SELECT delegation_json FROM coordination_delegations").fetchone(),
        )
    assert "merge-admin" not in json.loads(text)["gates"]


def test_wizard_changed_target_branch(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    script = answers(repo)
    script[3] = "release"
    ask, _ = scripted(script)
    said: list[str] = []
    assert run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=said.append)
    assert "merge:release" in "\n".join(said)


@pytest.mark.parametrize("branch", ["a b", "*", "release/*", "-x", "a..b"])
def test_wizard_rejects_globs_before_storing(repo: Path, tmp_path: Path, branch: str) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    before = counts(store)
    script = answers(repo)
    script[3] = branch
    ask, _ = scripted(script)
    with pytest.raises(BusError):
        run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=lambda _line: None)
    assert counts(store) == before


def test_wizard_other_coordinator_is_not_enrolled(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    store.register("claude", "lead", str(repo))
    script = answers(repo)
    script[-2] = "claude:lead"
    ask, _ = scripted(script)
    result = run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=lambda _line: None)
    assert result is not None
    assert result["enrolled"] is False
    assert "claude:lead" in str(result["next"])
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM coordination_enrollments").fetchone() == (0,)


def test_wizard_without_identity_picks_from_agents(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("claude", "lead", str(repo))
    script = answers(repo)
    script[-2] = "1"
    ask, prompts = scripted(script)
    result = run_wizard(store, "demo", str(repo), None, ask=ask, say=lambda _line: None)
    assert result is not None
    assert result["enrolled"] is False
    assert any("claude:lead" in prompt for prompt in prompts)


@pytest.mark.parametrize("label", ["", "  ", "x\x07y", "L" * 4097])
def test_wizard_validates_label_before_asking(repo: Path, tmp_path: Path, label: str) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    before = counts(store)
    ask, prompts = scripted(answers(repo))
    with pytest.raises(BusError):
        run_wizard(store, label, str(repo), "codex:owner", ask=ask, say=lambda _line: None)
    assert prompts == []
    assert counts(store) == before


def test_wizard_existing_project_fails_before_asking(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    ask, _ = scripted(answers(repo))
    assert run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=lambda _line: None)
    before = counts(store)
    ask, prompts = scripted(answers(repo))
    with pytest.raises(BusError, match="already exists"):
        run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=lambda _line: None)
    assert prompts == []
    assert counts(store) == before


def test_wizard_enter_keeps_every_gate_enabled(repo: Path, tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "owner", str(repo))
    ask, _ = scripted(answers(repo, gates=("",) * 4))
    said: list[str] = []
    assert run_wizard(store, "demo", str(repo), "codex:owner", ask=ask, say=said.append)
    screen = "\n".join(said)
    assert all(f"{gate}:develop" in screen for gate in GATES)


def test_setup_without_identity_stores_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    db = tmp_path / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(db))
    monkeypatch.chdir(repo)
    assert main(["coordinate", "demo", "--setup"]) == 0
    assert counts(Store(db)) == (0, 0, 0)


def test_terminal_setup_routes_to_wizard(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "owner")
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    calls: list[tuple[str, str | None]] = []

    def fake_wizard(
        _store: Store, label: str, _cwd: str, identity: str | None
    ) -> dict[str, object]:
        calls.append((label, identity))
        return {"wizard": True}

    monkeypatch.setattr("llm_bus.cli.app.run_wizard", fake_wizard)
    assert main(["coordinate", "demo", "--setup"]) == 0
    assert calls == [("demo", "claude:owner")]
    assert main(["--format", "agent", "coordinate", "other", "--setup"]) == 0
    assert len(calls) == 1
