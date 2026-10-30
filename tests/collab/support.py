"""collab test support."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import os
import shutil
import sqlite3
import subprocess
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import pytest

from llm_bus.collab.invites import encode_invite
from llm_bus.collab.models import Invite, NewMessage, Peer
from llm_bus.collab.policy import grant

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from llm_bus.collab.state import CollabState


def make_key(tmp_path: Path, user: str, *, publish: bool = True) -> Path:
    key = tmp_path / f"{user}_key"
    subprocess.run(
        [cast("str", shutil.which("ssh-keygen")), "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    if publish:
        published = tmp_path / "keys" / f"{user}.keys"
        existing = published.read_text() if published.exists() else ""
        published.write_text(existing + key.with_name(key.name + ".pub").read_text())
    return key


def sample_invite(
    *,
    host_user: str = "alice",
    mode: str = "readonly",
    expires_at: str = "2999-01-01T00:00:00.000Z",
) -> Invite:
    return Invite(
        v=0,
        ticket="ticket",
        nonce="n" * 32,
        host_user=host_user,
        joiner_user="bob",
        repository_name="repo",
        grant=mode,
        expires_at=expires_at,
    )


def sample_peer(
    *, mode: str = "full", full_until: str | None = None, session: str | None = None
) -> Peer:
    return Peer(
        responder_context="fork",
        status_summary="",
        id=1,
        pair="p",
        github_user="bob",
        repository="/repo",
        endpoint_id="e",
        ticket=None,
        mode=mode,
        full_until=full_until,
        review_replies=True,
        agent_replies=False,
        wake_on_answer=False,
        their_grant=None,
        session=session,
        confirm_code="c",
        created_at="now",
        revoked_at=None,
    )


def responder_peer(session: str | None) -> Peer:
    return sample_peer(mode="readonly", session=session)


def add_full_peer(
    state: CollabState, session: str, *, mode: str = "full", agent_replies: bool = False
) -> None:
    with contextlib.closing(sqlite3.connect(state.path)) as db:
        db.execute(
            "INSERT INTO peers(pair, github_user, repository, endpoint_id, key_fingerprint, mode, "
            "full_until, review_replies, agent_replies, session, confirm_code, created_at) "
            "VALUES ('p', 'bob', '/repo', 'e', 'f', ?, ?, 1, ?, ?, 'c', 'now')",
            (mode, grant("full", 5).full_until, int(agent_replies), session),
        )
        db.commit()


def fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> int:
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=5)
    try:
        connection.request("POST" if data is not None else "GET", parts.path, data, headers or {})
        return connection.getresponse().status
    finally:
        connection.close()


def committed_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text("VALUE = 1\n")
    git = cast("str", shutil.which("git"))
    for args in (["init", "-q"], ["add", "api.py"], ["commit", "-q", "-m", "init"]):
        subprocess.run(
            [git, "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repository), *args],
            check=True,
        )
    return repository


def invite_text(state: CollabState, ticket: str, repository: Path) -> str:
    nonce, expires = state.create_invite(
        "bob", grant("readonly", review_replies=False), str(repository), "claude:a"
    )
    return encode_invite(
        Invite(
            v=0,
            ticket=ticket,
            nonce=nonce,
            host_user="alice",
            joiner_user="bob",
            repository_name="repo",
            grant="readonly",
            expires_at=expires,
        )
    )


needs_ssh = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen missing")


posix_only = pytest.mark.skipif(os.name == "nt", reason="fake responder is a POSIX script")


async def wait_until(check: Callable[[], bool]) -> None:
    for _ in range(100):
        if check():
            return
        await asyncio.sleep(0.1)
    pytest.fail("condition not reached")


async def pair_and_exchange(states: dict[str, CollabState], text: str, repository: Path) -> None:
    alice, bob, mallory = states["alice"], states["bob"], states["mallory"]
    impostor = mallory.add_join(text, str(repository), None)
    mallory.approve_join(impostor, grant("monitor"))
    await wait_until(lambda: mallory.join(impostor)[3] == "failed")
    assert alice.peers() == []
    join = bob.add_join(text, str(repository), "claude:b")
    bob.approve_join(join, grant("monitor"))
    await wait_until(lambda: bob.join(join)[3] == "connected")
    assert alice.peers()[0]["confirm_code"] == bob.join(join)[4]
    peer_id = bob.peers()[0]["id"]
    bob.add_message(peer_id, NewMessage("out", "request", "u1", "What are you on?", "queued"))
    await wait_until(lambda: any(m["state"] == "unread" for m in bob.messages()))
    reply = next(m for m in bob.messages() if m["direction"] == "in")
    assert (reply["kind"], reply["body"]) == ("reply", "I am editing src/api.py")
    alice.revoke(alice.peers()[0]["id"])
    bob.add_message(peer_id, NewMessage("out", "request", "u2", "Still there?", "queued"))
    await wait_until(lambda: any(m["state"] == "rejected" for m in bob.messages()))
