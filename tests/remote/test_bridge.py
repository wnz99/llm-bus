"""bridge."""

from __future__ import annotations

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast, final, override

import pytest

from llm_bus import main
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.policy.config import PeerPolicy
from llm_bus.remote.bridge import Bridge, run_bridge
from llm_bus.remote.outgoing import enqueue_remote
from llm_bus.remote.store import RemoteStore

from .support import FakeClock, delivery, setup_machine

if TYPE_CHECKING:
    from pathlib import Path


def test_bridge_lost_receipt_replays_once_then_syncs_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    pending = True
    lose_receipt = True
    acknowledged = False
    wake_calls: list[int] = []

    def request(_self: object, method: str, path: str, data: object = None) -> object:
        nonlocal pending, lose_receipt, acknowledged
        if path == "/v1/sessions":
            return {"address": "b/xyz/claude:recipient"}
        if path.startswith("/v1/sessions?"):
            return []
        if path.startswith("/v1/inbox?"):
            if pending and "after=0" in path:
                return [item]
            return []
        if path == "/v1/receipts":
            if lose_receipt:
                lose_receipt = False
                raise BusError("response lost")
            pending = False
            return {"status": "delivered_local"}
        if path == "/v1/ack":
            acknowledged = True
            return {"status": "acknowledged"}
        raise AssertionError((method, path, data))

    def record_wake(
        _recipient: str, message_id: int, _sender: str, **_kwargs: object
    ) -> dict[str, str]:
        wake_calls.append(message_id)
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    monkeypatch.setattr("llm_bus.wake.dispatch.wake", record_wake)
    clock = FakeClock()
    assert Bridge(store, config, clock=clock).sync()["errors"] == 1
    assert len(store.inbox("claude:recipient")) == 1
    clock.advance(5)
    Bridge(Store(store.path), config, clock=clock).sync()
    assert wake_calls == [store.inbox("claude:recipient")[0]["id"]]
    assert len(store.inbox("claude:recipient")) == 1
    store.acknowledge("claude:recipient", [store.inbox("claude:recipient")[0]["id"]])
    Bridge(Store(store.path), config, clock=clock).sync()
    assert acknowledged
    assert RemoteStore(store, config.connection).acknowledgements() == []


def test_cli_remote_send_queues_offline_and_native_inbox_reads_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, config = setup_machine(tmp_path, "a")
    path = tmp_path / "config.toml"
    path.write_text(f'''policy_version = 2
[remote]
url = "https://bus.test"
machine_id = "a"
credential_file = "a.token"
[[repositories]]
path = "{(tmp_path / "repo").as_posix()}"
repo_id = "xyz"
[[repositories.peers]]
machine_id = "b"
send_messages = true
''')
    monkeypatch.setenv("LLM_BUS_CONFIG", str(path))
    monkeypatch.setenv("LLM_BUS_DB", str(store.path))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "sender")
    monkeypatch.chdir(tmp_path / "repo")
    assert main(["send", "b/xyz/claude:recipient", "--body", "offline"]) == 0
    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["status"] == "queued_local"
    assert main(["send", "b/other/claude:recipient", "--cross-folder", "--body", "deny"]) == 1
    assert len(RemoteStore(store, config.connection).outgoing()) == 1


def test_continuous_bridge_retries_truncated_http_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Explicit fake time keeps HTTP retry behavior deterministic.
    # pylint: disable=too-many-locals
    store, config = setup_machine(tmp_path, "a")
    uploads: list[tuple[object, str | None]] = []

    @final
    class Handler(BaseHTTPRequestHandler):
        # These method names, parameter names, and connection state belong to the stdlib API.
        # pylint: disable=invalid-name,redefined-builtin,attribute-defined-outside-init
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def do_POST(self) -> None:
            payload = cast(
                "object", json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            if self.path == "/v1/messages":
                uploads.append((payload, self.headers.get("User-Agent")))
                if len(uploads) == 1:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b'20\r\n{"status":')
                    self.close_connection = True
                    return
            self.end_headers()
            self.wfile.write(b'{"status":"accepted_remote"}')

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = replace(
        config,
        url=f"http://127.0.0.1:{server.server_port}",
        repositories=(
            replace(config.repositories[0], peers=(PeerPolicy("b", send_messages=True),)),
        ),
    )
    sender = SenderContext("codex", "sender", str(tmp_path / "repo"))
    sent = enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "retry me")
    states: list[str] = []
    clock = FakeClock()

    def next_pass(_seconds: float) -> None:
        clock.advance(_seconds)
        states.append(RemoteStore(Store(store.path), config.connection).outgoing()[0][1])
        if len(states) == 2:
            raise KeyboardInterrupt

    def forbid_wake(*_args: object, **_kwargs: object) -> None:
        pytest.fail("HTTP retries must not wake a real agent")

    monkeypatch.setattr("llm_bus.remote.bridge.load_config", lambda: config)
    # Subprocess timeout waits need the real clock.
    monkeypatch.setattr("llm_bus.remote.bridge.time", SimpleNamespace(sleep=next_pass))
    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbid_wake)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_bridge(store, once=False, clock=clock)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert {agent for _, agent in uploads} == {"llm-bus/0.1"}
    assert states == ["queued_local", "accepted_remote"]
    assert len(uploads) == 2
    assert uploads[0] == uploads[1]
    assert cast("dict[str, object]", uploads[1][0])["id"] == sent["remote_id"]
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert results == [
        {"uploaded": 0, "imported": 0, "acknowledged": 0, "errors": 1},
        {"uploaded": 1, "imported": 0, "acknowledged": 0, "errors": 0},
    ]
