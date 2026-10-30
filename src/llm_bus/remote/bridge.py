"""Opt-in remote policy, authenticated HTTPS transport, and polling bridge."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.policy.config import Config, load_config
from llm_bus.remote import batches, discovery, incoming
from llm_bus.remote.credentials import read_token
from llm_bus.remote.http import Relay, RelayError
from llm_bus.remote.store import RemoteStore
from llm_bus.reports import sync
from llm_bus.wake.dispatch import dispatch_due

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_bus.mailbox.store import Store

SYNC_INCOMPLETE = "Remote synchronization incomplete; pending state retained"


class Bridge:
    # pylint: disable=too-many-instance-attributes
    config: Config
    store: Store
    remote: RemoteStore
    relay: Relay
    clock: Callable[[], datetime]
    refresh_status: bool

    def __init__(
        self,
        store: Store,
        config: Config,
        *,
        clock: Callable[[], datetime] | None = None,
        refresh_status: bool = False,
    ) -> None:
        self.config = config
        self.store = store
        self.remote = RemoteStore(store, config.connection)
        self.relay = Relay(config)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.refresh_status = refresh_status
        self.unsupported_until: dict[str, datetime] = {}
        self.reply_capability: tuple[datetime, bool] | None = None

    def record(self, kind: str, key: str, error: BusError | None = None) -> None:
        code = (
            error.code if isinstance(error, RelayError) else ("protocol_error" if error else None)
        )
        self.remote.record_sync_result(kind, key, self.clock(), error_code=code)

    def current_config(self) -> Config | None:
        config = load_config()
        if config is None or config.connection != self.config.connection:
            return None
        return config

    def sync(self) -> dict[str, int]:
        current = self.current_config()
        if current is None:
            raise BusError("Remote policy unavailable or connection changed")
        self.config = current
        self.relay.config = current
        self.relay.token = read_token(current.credential_file)
        counts = {"uploaded": 0, "imported": 0, "acknowledged": 0, "errors": 0}
        counts["errors"] += discovery.publish_sessions(self)
        for repo in self.config.repositories:
            snapshot = self.remote.peer_snapshot(repo.repo_id, self.clock())
            due = cast("str | None", snapshot["next_attempt_at"])
            if due is not None and self.clock() < datetime.fromisoformat(due):
                continue
            try:
                if any(peer.send_messages for peer in repo.peers):
                    discovery.cache_peers(self, repo)
                    self.remote.record_peer_result(repo.repo_id, self.clock(), error_code=None)
            except BusError as error:
                code = error.code if isinstance(error, RelayError) else "protocol_error"
                self.remote.record_peer_result(repo.repo_id, self.clock(), error_code=code)
                counts["errors"] += 1
        counts["uploaded"], failures = batches.sync_outgoing(self)
        counts["errors"] += failures
        for repo in self.config.repositories:
            try:
                if any(peer.receive_messages in {"inbox", "monitor"} for peer in repo.peers):
                    counts["imported"] += incoming.receive(self, repo)
            except BusError:
                counts["errors"] += 1
        counts["acknowledged"], failures = incoming.sync_acknowledgements(self)
        counts["errors"] += failures
        counts["errors"] += sync.submit_reports(self)
        counts["errors"] += sync.poll_reports(self)
        dispatch_due(self.store)
        return counts


def run_bridge(
    store: Store,
    *,
    once: bool,
    refresh_status: bool = False,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    if refresh_status and not once:
        raise BusError("--refresh-status requires --once")
    bridge: Bridge | None = None
    pause = sleep or time.sleep
    failures = 0
    while True:
        config = load_config()
        if config is None:
            raise BusError("Remote bridge requires a remote configuration file")
        try:
            if bridge is None or bridge.config.connection != config.connection:
                bridge = Bridge(store, config, refresh_status=refresh_status, clock=clock)
            result = bridge.sync()
            failures = 0
        except BusError as error:
            if once:
                raise
            failures += 1
            print(json.dumps({"error": str(error)}), flush=True)
            pause((5, 10, 20, 30)[min(failures - 1, 3)])
            continue
        print(json.dumps(result), flush=True)
        if once and result.get("errors"):
            raise BusError(SYNC_INCOMPLETE)
        if once:
            return
        pause(bridge.remote.sleep_seconds(bridge.clock()))
