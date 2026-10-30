"""Collaboration daemon: pairing handshake, owner policy for incoming text, and the outbox."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, NamedTuple

from llm_bus.collab import identity
from llm_bus.collab.invites import decode_invite, pair_id
from llm_bus.collab.models import (
    JOIN_CONNECTED,
    JOIN_FAILED,
    MODES,
    ApprovedJoin,
    CollabError,
    NewPeer,
)
from llm_bus.collab.protocol import REJECTED
from llm_bus.collab.transport import Frame, Transport, TransportError

if TYPE_CHECKING:
    from llm_bus.collab.daemon import Daemon


class Hello(NamedTuple):
    """A joiner's claim, still unverified."""

    nonce: str
    user: str
    signature: str
    their_grant: str
    ticket: str


def _hello_fields(frame: Frame) -> Hello | None:
    values = [frame.get(key) for key in ("nonce", "user", "sig", "grant", "ticket")]
    strings = [value for value in values if isinstance(value, str)]
    if len(strings) != len(values):
        return None
    hello = Hello(*strings)
    return hello if hello.their_grant in MODES else None


def accept_hello(daemon: Daemon, remote: str, frame: Frame) -> Frame:
    """Host side: verify the invited GitHub user signed this exact connection."""
    hello = _hello_fields(frame)
    if (
        hello is None
        or Transport.ticket_id(hello.ticket) != remote
        or not daemon.state.invite_is_open(hello.nonce, hello.user)
    ):
        return REJECTED
    me, key = daemon.local_identity()
    handshake = identity.Handshake(
        daemon.bound_transport().endpoint_id, remote, hello.nonce, me, hello.user
    )
    key_fingerprint = identity.verify(
        hello.user, identity.transcript("joiner", handshake), hello.signature
    )
    if key_fingerprint is None:
        return REJECTED
    choice, repository, session = daemon.state.claim_invite(hello.nonce, hello.user)
    facts = NewPeer(
        pair_id(hello.nonce),
        hello.user,
        repository,
        remote,
        hello.ticket,
        key_fingerprint,
        hello.their_grant,
        session,
        identity.confirm_code(handshake),
    )
    daemon.state.add_peer(facts, choice)
    host_proof = identity.transcript("host", handshake)
    return {"ok": True, "sig": identity.sign(key, host_proof), "grant": choice.mode}


async def process_join(daemon: Daemon, join: ApprovedJoin) -> None:
    try:
        facts = await handshake_as_joiner(daemon, join)
        daemon.state.add_peer(facts, join.choice)
        daemon.state.finish_join(join.id, JOIN_CONNECTED, facts.confirm_code)
    except (CollabError, TransportError, sqlite3.Error, ValueError) as exc:
        daemon.state.finish_join(join.id, JOIN_FAILED, str(exc))


async def handshake_as_joiner(daemon: Daemon, join: ApprovedJoin) -> NewPeer:
    """Joiner side: prove our GitHub identity, then require the host's proof."""
    me, key = daemon.local_identity()
    invite = decode_invite(join.invite)
    if invite["joiner_user"].lower() != me.lower():
        raise CollabError(f"Invite is for @{invite['joiner_user']}, not @{me}")
    transport = daemon.bound_transport()
    handshake = identity.Handshake(
        Transport.ticket_id(invite["ticket"]),
        transport.endpoint_id,
        invite["nonce"],
        invite["host_user"],
        me,
    )
    proof = identity.transcript("joiner", handshake)
    response = await transport.request(
        invite["ticket"],
        {
            "type": "hello",
            "nonce": invite["nonce"],
            "user": me,
            "sig": await asyncio.to_thread(identity.sign, key, proof),
            "grant": join.choice.mode,
            "ticket": transport.ticket(),
        },
    )
    host_signature = response.get("sig")
    if response.get("ok") is not True or not isinstance(host_signature, str):
        # pylint: disable-next=clean-code-business-policy-literal
        raise CollabError("The inviter rejected this join; the invite may be used or expired")
    key_fingerprint = await asyncio.to_thread(
        identity.verify,
        invite["host_user"],
        identity.transcript("host", handshake),
        host_signature,
    )
    if key_fingerprint is None:
        raise CollabError(f"The inviter could not prove they are @{invite['host_user']}")
    their_grant = response.get("grant")
    return NewPeer(
        pair_id(invite["nonce"]),
        invite["host_user"],
        join.repository,
        handshake.host_id,
        invite["ticket"],
        key_fingerprint,
        their_grant if isinstance(their_grant, str) else "unknown",
        join.session,
        identity.confirm_code(handshake),
    )
