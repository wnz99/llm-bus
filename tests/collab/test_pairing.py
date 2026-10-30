"""pairing."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.collab import identity
from llm_bus.collab.daemon import Daemon
from llm_bus.collab.exposure import exposure_allowed
from llm_bus.collab.invites import decode_invite, encode_invite
from llm_bus.collab.models import CollabError
from llm_bus.collab.policy import effective_mode, grant
from llm_bus.collab.state import CollabState
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store

from .support import (
    add_full_peer,
    committed_repository,
    invite_text,
    make_key,
    needs_ssh,
    pair_and_exchange,
    posix_only,
    sample_invite,
    sample_peer,
    wait_until,
)

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.collab.models import Invite


def test_invite_round_trips_and_rejects_tampering() -> None:
    invite = sample_invite()
    assert decode_invite(encode_invite(invite)) == invite
    with pytest.raises(CollabError, match="expired"):
        decode_invite(encode_invite(sample_invite(expires_at="2000-01-01T00:00:00.000Z")))
    with pytest.raises(CollabError):
        decode_invite(encode_invite(sample_invite(mode="root")))
    with pytest.raises(CollabError):
        decode_invite(encode_invite(sample_invite(host_user="-bad")))
    with pytest.raises(CollabError):
        decode_invite(
            encode_invite(cast("Invite", cast("object", {**sample_invite(), "extra": "x"})))
        )
    with pytest.raises(CollabError):
        decode_invite("https://example.com/not-an-invite")


def test_full_mode_requires_expiry_and_falls_back_to_monitor() -> None:
    with pytest.raises(CollabError):
        grant("full")
    with pytest.raises(CollabError):
        grant("admin")
    choice = grant("full", 5)
    peer = sample_peer(full_until=choice.full_until)
    assert effective_mode(peer) == "full"
    assert effective_mode(sample_peer(full_until="2000-01-01T00:00:00.000Z")) == "monitor"
    assert effective_mode({**peer, "revoked_at": "now"}) is None


def test_invites_are_single_use_and_bound_to_one_user(isolated_collab: Path) -> None:
    state = CollabState(isolated_collab)
    nonce, _ = state.create_invite("bob", grant("readonly"), "/repo", "claude:me")
    assert state.invite_is_open(nonce, "bob")
    assert not state.invite_is_open(nonce, "mallory")
    with pytest.raises(CollabError):
        state.claim_invite(nonce, "mallory")
    choice, repository, session = state.claim_invite(nonce, "bob")
    assert (choice.mode, repository, session) == ("readonly", "/repo", "claude:me")
    with pytest.raises(CollabError):
        state.claim_invite(nonce, "bob")
    late, _ = state.create_invite("bob", grant("monitor"), "/repo", None)
    with contextlib.closing(sqlite3.connect(state.path)) as db:
        db.execute(
            "UPDATE invites SET expires_at = '2000-01-01T00:00:00.000Z' WHERE nonce = ?", (late,)
        )
        db.commit()
    with pytest.raises(CollabError, match="expired"):
        state.claim_invite(late, "bob")


@needs_ssh
def test_signature_binds_github_key_to_exact_transcript(tmp_path: Path) -> None:
    alice = make_key(tmp_path, "alice")
    make_key(tmp_path, "mallory")
    handshake = identity.Handshake("host", "joiner", "nonce", "bob", "alice")
    data = identity.transcript("joiner", handshake)
    signature = identity.sign(alice, data)
    assert identity.verify("alice", data, signature) is not None
    assert identity.verify("mallory", data, signature) is None
    assert identity.verify("nobody", data, signature) is None
    other = identity.transcript("joiner", handshake._replace(joiner_id="attacker"))
    assert identity.verify("alice", other, signature) is None
    assert identity.verify("alice", identity.transcript("host", handshake), signature) is None
    assert identity.confirm_code(handshake) == identity.confirm_code(handshake)


def test_peer_text_reaches_agents_only_in_full_mode(tmp_path: Path, isolated_collab: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    me = store.register("claude", "me", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, me)
    store.deliver_collab("collab:_daemon", me, "notice without peer text")
    peer_message = store.deliver_collab("collab:bob:p", me, "peer text")
    assert [m["sender"] for m in store.inbox(me)] == ["collab:_daemon", "collab:bob:p"]
    assert (peer_message, "collab:bob:p") in store.pending_members(me)
    assert store.pending_allowed(peer_message)
    state.set_mode(state.peers()[0]["id"], grant("readonly"))
    assert [m["sender"] for m in store.inbox(me)] == ["collab:_daemon"]
    assert (peer_message, "collab:bob:p") not in store.pending_members(me)
    assert not store.pending_allowed(peer_message)
    assert store.pending_count(me) == 1
    assert not exposure_allowed("collab:bob:p", "claude:someone-else")
    assert all(a["address"] != "collab:bob:p" for a in store.agents())
    with pytest.raises(BusError):
        store.send(SenderContext("claude", "me", str(tmp_path)), "collab:bob:p", "hello")


@needs_ssh
@posix_only
def test_two_daemons_pair_reject_impersonation_and_respond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = committed_repository(tmp_path)
    fake = tmp_path / "fakeclaude"
    fake.write_text('#!/bin/sh\necho \'{"result":"I am editing src/api.py","is_error":false}\'\n')
    fake.chmod(0o700)
    monkeypatch.setenv("LLM_BUS_COLLAB_CLAUDE", str(fake))
    states: dict[str, CollabState] = {}
    daemons: dict[str, Daemon] = {}
    for user in ("alice", "bob", "mallory"):
        states[user] = CollabState(tmp_path / user)
        states[user].set_identity(user, make_key(tmp_path, user))
        daemons[user] = Daemon(states[user], Store(tmp_path / f"{user}.sqlite3"))
    states["mallory"].set_identity("bob", tmp_path / "mallory_key")

    async def scenario() -> None:
        tasks = [asyncio.create_task(d.run()) for d in daemons.values()]
        try:
            await wait_until(lambda: all(d.transport is not None for d in daemons.values()))
            transport = daemons["alice"].transport
            assert transport is not None
            await pair_and_exchange(
                states, invite_text(states["alice"], transport.ticket(), repository), repository
            )
        finally:
            for task in tasks:
                _ = task.cancel()
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
