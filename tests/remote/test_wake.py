"""wake."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import llm_bus.wake.adapters
import llm_bus.wake.dispatch
from llm_bus import main
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.store import RemoteStore
from tests.support import assert_wake_coverage, prompt_input

from .support import FakeClock, admit_delivery, delivery, setup_machine


@pytest.mark.parametrize("arrival_during_call", [False, True])
def test_remote_admission_releases_unsupported_wake_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, arrival_during_call: bool
) -> None:
    def forbid_native(*_args: object, **_kwargs: object) -> None:
        pytest.fail("native adapter must never execute")

    monkeypatch.setattr(llm_bus.wake.adapters, "_wake_codex", forbid_native)
    monkeypatch.setattr(llm_bus.wake.adapters, "_wake_claude", forbid_native)
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    clock = FakeClock()
    calls: list[int] = []
    first, second = delivery(), delivery()
    first_admission = remote.admit_message(first, config.repositories[0].scope)
    assert first_admission is not None
    assert first_admission[2]

    def native(
        _recipient: str, key: int, _sender: str, **_kwargs: object
    ) -> llm_bus.wake.adapters.WakeResult:
        calls.append(key)
        if arrival_during_call and len(calls) == 1:
            admission = remote.admit_message(second, config.repositories[0].scope)
            assert admission is not None
            assert admission[2]
        return {"status": "unsupported", "via": "test"}

    monkeypatch.setattr(llm_bus.wake.dispatch, "wake", native)
    monkeypatch.setattr(llm_bus.wake.dispatch, "_now", clock)
    llm_bus.wake.dispatch.dispatch_due(store)
    assert len(calls) == (2 if arrival_during_call else 1)
    clock.advance(600)
    # Relay replay and elapsed time alone must not retry an unsupported endpoint.
    assert remote.admit_message(first, config.repositories[0].scope) == (
        *first_admission[:2],
        False,
    )
    llm_bus.wake.dispatch.dispatch_due(Store(store.path))
    assert len(calls) == (2 if arrival_during_call else 1)
    if not arrival_during_call:
        admission = remote.admit_message(second, config.repositories[0].scope)
        assert admission is not None
        assert admission[2]
        llm_bus.wake.dispatch.dispatch_due(Store(store.path))
    assert len(calls) == 2
    clock.advance(600)
    llm_bus.wake.dispatch.dispatch_due(Store(store.path))
    assert len(calls) == 2
    assert_wake_coverage(store)
    assert store.pending_count("claude:recipient") == 2


def test_wake_coverage_excludes_revoked_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    store, config = setup_machine(tmp_path, "b")
    native = store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    admitted = RemoteStore(store, config.connection).admit_message(
        item, config.repositories[0].scope
    )
    assert admitted is not None
    local = store.send(
        SenderContext("codex", "local", str(tmp_path / "repo")), native, "local body"
    )
    snapshot = llm_bus.wake.dispatch._snapshot  # pyright: ignore[reportPrivateUsage]
    path = Path(os.environ["LLM_BUS_CONFIG"])

    def revoke_after_snapshot(
        store: Store, recipient: str, claim: llm_bus.wake.dispatch.Claim
    ) -> llm_bus.wake.dispatch.Batch:
        batch = snapshot(store, recipient, claim)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'receive_messages = "inbox"', 'receive_messages = "deny"'
            ),
            encoding="utf-8",
        )
        return batch

    calls: list[int] = []

    def native_wake(_recipient: str, key: int, _sender: str, **_kwargs: object) -> dict[str, str]:
        calls.append(key)
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr(llm_bus.wake.dispatch, "_snapshot", revoke_after_snapshot)
    monkeypatch.setattr(llm_bus.wake.dispatch, "wake", native_wake)
    llm_bus.wake.dispatch.dispatch_pending(store, native)
    assert calls == [local["id"]]
    with store.transaction() as db:
        assert db.execute("SELECT message_id FROM wake_coverage").fetchall() == [(local["id"],)]
    assert len(store.history(folder=None)) == 2


def test_bridge_recovers_receipted_legacy_wake_claim_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    admitted = admit_delivery(store, config, item)
    assert admitted is not None
    with store.transaction() as db:
        db.execute("UPDATE remote_imports SET wake_claimed=1")
        db.execute("DROP TABLE wake_coverage")
        db.execute("DROP TABLE wake_dispatch")
        db.execute("DROP TABLE hook_guidance")
        db.execute("PRAGMA user_version=2")
    store = Store(store.path)

    def request(*_args: object, **_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["errors"] == 0
    with store.transaction() as db:
        assert db.execute("SELECT status FROM wake_coverage").fetchone() == ("requested",)
        assert db.execute("SELECT wake_claimed FROM remote_imports").fetchone() == (1,)


def test_policy_reload_after_wake_claim_prevents_native_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original_claim = llm_bus.wake.dispatch._claim  # pyright: ignore[reportPrivateUsage]

    def revoke_before_dispatch(
        store: Store, recipient: str, *, trigger_id: int | None
    ) -> llm_bus.wake.dispatch.Claim | None:
        claim = original_claim(store, recipient, trigger_id=trigger_id)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'receive_messages = "inbox"', 'receive_messages = "deny"'
            ),
            encoding="utf-8",
        )
        return claim

    def request(_self: object, _method: str, route: str, _data: object = None) -> object:
        return [item] if route.startswith("/v1/inbox?") and "after=0" in route else []

    def forbid_wake(*_args: object, **_kwargs: object) -> None:
        pytest.fail("revocation must prevent native wake")

    monkeypatch.setattr("llm_bus.wake.dispatch._claim", revoke_before_dispatch)
    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbid_wake)
    assert Bridge(store, config).sync()["errors"] == 0
    assert store.pending_count("claude:recipient") == 0
    assert len(store.history(folder=None)) == 1


@pytest.mark.parametrize(
    "invalid", ["broken = [", 'policy_version = 2\n[remote]\nurl = "https://["']
)
def test_invalid_remote_configuration_keeps_native_commands_and_hooks_working(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid: str,
) -> None:
    store, _config = setup_machine(tmp_path, "b")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(invalid, encoding="utf-8")
    folder = str(tmp_path / "repo")
    recipient = store.register("codex", "recipient", folder)
    store.send(SenderContext("claude", "sender", folder), recipient, "local")
    assert store.pending_count(recipient) == 1
    assert store.inbox(recipient)[0]["body"] == "local"
    monkeypatch.setenv("LLM_BUS_DB", str(store.path))
    monkeypatch.setenv("CODEX_THREAD_ID", "recipient")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(folder)
    assert main(["list"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(
        "sys.stdin",
        prompt_input("recipient", folder),
    )
    assert main(["hook", "codex"]) == 0
    assert "1 pending message(s)" in capsys.readouterr().out
