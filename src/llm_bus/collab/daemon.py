"""Collaboration daemon: pairing handshake, owner policy for incoming text, and the outbox."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from llm_bus.collab import delivery, pairing
from llm_bus.collab.models import (
    MESSAGE_CANCELLED,
    MESSAGE_REJECTED,
    MESSAGE_SENT,
    CollabError,
    Message,
)
from llm_bus.collab.protocol import REJECTED
from llm_bus.collab.runtime import daemon_file
from llm_bus.collab.state import CollabState
from llm_bus.collab.transport import Frame, Transport, TransportError
from llm_bus.collab.web.http import serve_ui
from llm_bus.errors import BusError
from llm_bus.mailbox.connection import default_path
from llm_bus.mailbox.store import Store

if TYPE_CHECKING:
    from collections.abc import Coroutine

PUMP_SECONDS = 2
PAGE_TOKEN_BYTES = 24
MIN_PAGE_TOKEN_CHARS = 32
RETRY_SECONDS = 15


class Daemon:
    """One per collaboration identity.

    Below full mode, peer text reaches the main agent only as the first answer to a question that
    agent asked, and only while the owner allows agent replies for that peer.
    """

    state: CollabState
    store: Store
    transport: Transport | None
    ui_url: str
    _retry_after: dict[int, float]
    _sending: set[int]
    _tasks: set[asyncio.Task[None]]

    def __init__(self, state: CollabState, store: Store) -> None:
        self.state = state
        self.store = store
        self.transport = None
        self.ui_url = ""
        self._retry_after = {}
        self._sending = set()
        self._tasks = set()

    def local_identity(self) -> tuple[str, Path]:
        found = self.state.identity()
        if found is None:
            raise CollabError("No collaboration identity; run `llm-bus collab setup` first")
        return found[0], Path(found[1])

    def spawn(self, work: Coroutine[object, object, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def bound_transport(self) -> Transport:
        if self.transport is None:
            raise RuntimeError("Transport is not bound")
        return self.transport

    def _page_address(self) -> tuple[str, int]:
        """Reuse the owner page token and port so links survive daemon restarts."""
        path = self.state.home / "page.json"
        try:
            saved = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            saved = {}
        token, port = saved.get("token"), saved.get("port")
        if not isinstance(token, str) or len(token) < MIN_PAGE_TOKEN_CHARS:
            token = secrets.token_urlsafe(PAGE_TOKEN_BYTES)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        return token, port if isinstance(port, int) else 0

    async def run(self) -> None:
        self.local_identity()
        transport = await Transport.bind(
            self.state.home / "endpoint.key", self.state.home / "endpoint.port"
        )
        self.transport = transport
        token, saved_port = await asyncio.to_thread(self._page_address)
        server, port = serve_ui(self.state, self.store, token, transport.ticket, saved_port)
        await asyncio.to_thread(
            (self.state.home / "page.json").write_text, json.dumps({"token": token, "port": port})
        )
        self.ui_url = f"http://127.0.0.1:{port}/{token}/"
        details = {"pid": os.getpid(), "url": self.ui_url, "endpoint_id": transport.endpoint_id}
        path = daemon_file(self.state)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(details, stream)
        _ = sys.stdout.write(json.dumps(details) + "\n")
        sys.stdout.flush()
        try:
            await asyncio.gather(transport.serve(self.handle), self.pump())
        finally:
            server.shutdown()
            path.unlink(missing_ok=True)
            await transport.close()

    async def handle(self, remote: str, frame: Frame) -> Frame:
        """Dispatch one authenticated frame; unknown endpoints may only present an invite."""
        try:
            if frame.get("type") == "hello":
                return await asyncio.to_thread(pairing.accept_hello, self, remote, frame)
            if frame.get("type") == "msg":
                return delivery.incoming(self, remote, frame)
        except (CollabError, BusError, sqlite3.Error, OSError):
            return REJECTED
        return REJECTED

    async def pump(self) -> None:
        while True:
            for join in self.state.approved_joins():
                await pairing.process_join(self, join)
            for message in reversed(self.state.messages(state="queued")):
                if message["id"] not in self._sending:
                    self._sending.add(message["id"])
                    self.spawn(self._send(message))
            await asyncio.sleep(PUMP_SECONDS)

    async def _send(self, message: Message) -> None:
        try:
            await self._deliver_outgoing(message)
        finally:
            self._sending.discard(message["id"])

    async def _deliver_outgoing(self, message: Message) -> None:
        loop = asyncio.get_running_loop()
        if self._retry_after.get(message["id"], 0) > loop.time():
            return
        peer = self.state.peer(message["peer_id"])
        if peer is None or peer["revoked_at"] is not None or not peer["ticket"]:
            self.state.set_state(message["id"], MESSAGE_CANCELLED, "peer revoked or unreachable")
            return
        frame: Frame = {
            "type": "msg",
            "pair": peer["pair"],
            "uid": message["uid"],
            "kind": message["kind"],
            "in_reply_to": message["in_reply_to"],
            "body": message["body"],
            "ticket": self.bound_transport().ticket(),
        }
        try:
            response = await self.bound_transport().request(peer["ticket"], frame)
        except TransportError as exc:
            self._retry_after[message["id"]] = loop.time() + RETRY_SECONDS
            self.state.set_state(message["id"], "queued", str(exc))
            return
        accepted = response.get("ok") is True
        self.state.set_state(message["id"], MESSAGE_SENT if accepted else MESSAGE_REJECTED)


def run_daemon() -> None:
    try:
        asyncio.run(Daemon(CollabState(), Store(default_path())).run())
    except KeyboardInterrupt:
        sys.exit(0)
