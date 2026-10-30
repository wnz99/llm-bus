"""answers."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from typing import TYPE_CHECKING

import pytest

from llm_bus.collab.daemon import Daemon
from llm_bus.collab.exposure import exposure_allowed
from llm_bus.collab.models import NewMessage
from llm_bus.collab.policy import grant
from llm_bus.collab.responder import Reply
from llm_bus.collab.state import CollabState
from llm_bus.mailbox.store import Store

from .support import add_full_peer, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.collab.transport import Frame


def test_answers_reach_the_asking_agent_only_while_allowed(
    tmp_path: Path, isolated_collab: Path
) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    me = store.register("claude", "me", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, me, mode="readonly", agent_replies=True)
    _ = state.add_message(
        state.peers()[0]["id"],
        NewMessage("out", "request", "q1", "Question", "sent", origin_session=me),
    )
    store.deliver_collab("collab:reply:bob:p:q1", me, "answer")
    store.deliver_collab("collab:bob:p", me, "unsolicited")
    assert [m["sender"] for m in store.inbox(me)] == ["collab:reply:bob:p:q1"]
    assert not exposure_allowed("collab:reply:bob:p:q1", "claude:someone-else")
    state.set_mode(state.peers()[0]["id"], grant("readonly"))
    assert store.inbox(me) == []
    state.set_mode(state.peers()[0]["id"], grant("readonly", agent_replies=True))
    state.revoke(state.peers()[0]["id"])
    assert store.inbox(me) == []


@pytest.mark.parametrize("answer", [False, True])
def test_other_pairing_cannot_restore_revoked_text(
    tmp_path: Path, isolated_collab: Path, *, answer: bool
) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    me = store.register("claude", "me", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, me, agent_replies=answer)
    _ = state.add_message(
        state.peers()[0]["id"],
        NewMessage("out", "request", "q1", "Question", "sent", origin_session=me),
    )
    sender = "collab:reply:bob:p:q1" if answer else "collab:bob:p"
    store.deliver_collab(sender, me, "old pairing text")
    assert len(store.inbox(me)) == 1
    state.revoke(state.peers()[0]["id"])
    with state.transaction() as db:
        db.execute("UPDATE peers SET pair = 'old' WHERE pair = 'p'")
    add_full_peer(state, me, agent_replies=answer)
    with state.transaction() as db:
        db.execute(
            "UPDATE peers SET pair = 'new', repository = '/other', endpoint_id = 'other' WHERE pair = 'p'"
        )
    assert store.inbox(me) == []
    assert not exposure_allowed("collab:bob", me)


def test_only_the_first_answer_to_an_own_question_is_delivered(
    tmp_path: Path, isolated_collab: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_wake(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "skipped", "via": "test"}

    monkeypatch.setattr("llm_bus.collab.delivery.dispatch_pending", no_wake)
    store = Store(tmp_path / "bus.sqlite3")
    me = store.register("claude", "me", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, me, mode="monitor", agent_replies=True)
    peer = state.peers()[0]
    daemon = Daemon(state, store)
    _ = state.add_message(
        peer["id"], NewMessage("out", "request", "q1", "How?", "sent", origin_session=me)
    )

    def arrive(uid: str, in_reply_to: str | None) -> str:
        frame: Frame = {
            "type": "msg",
            "pair": "p",
            "uid": uid,
            "kind": "reply" if in_reply_to else "request",
            "in_reply_to": in_reply_to,
            "body": f"text {uid}",
        }
        assert asyncio.run(daemon.handle("e", frame)) == {"ok": True}
        return next(m["state"] for m in state.messages() if m["uid"] == uid)

    assert arrive("a1", "q1") == "delivered"
    assert arrive("a2", "q1") == "unread"
    assert arrive("a3", "never-asked") == "unread"
    assert arrive("a4", None) == "unread"
    answers = [m for m in store.inbox(me) if m["sender"] == "collab:reply:bob:p:q1"]
    assert len(answers) == 1
    assert "untrusted peer data" in answers[0]["body"]
    assert "text a1" in answers[0]["body"]
    state.set_mode(peer["id"], grant("monitor"))
    _ = state.add_message(
        peer["id"], NewMessage("out", "request", "q2", "And?", "sent", origin_session=me)
    )
    assert arrive("a5", "q2") == "unread"
    assert all(not m["sender"].startswith("collab:reply:") for m in store.inbox(me))


@pytest.mark.parametrize("mode", ["monitor", "full"])
def test_answers_keep_the_question_owner_after_peer_rebinding(
    tmp_path: Path, isolated_collab: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    def no_dispatch(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr("llm_bus.collab.delivery.dispatch_pending", no_dispatch)
    store = Store(tmp_path / "bus.sqlite3")
    first = store.register("claude", "first", str(tmp_path))
    second = store.register("codex", "second", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, first, mode=mode, agent_replies=mode != "full")
    peer_id = state.peers()[0]["id"]
    _ = state.add_message(
        peer_id, NewMessage("out", "request", "q1", "First question", "sent", origin_session=first)
    )
    state.set_session(peer_id, second)
    daemon = Daemon(state, store)
    frame: Frame = {
        "type": "msg",
        "pair": "p",
        "uid": "a1",
        "kind": "reply",
        "in_reply_to": "q1",
        "body": "Answer for first",
    }
    assert asyncio.run(daemon.handle("e", frame)) == {"ok": True}
    assert len(store.inbox(first)) == 1
    assert store.inbox(second) == []
    state.set_session(peer_id, "claude:third")
    assert len(store.inbox(first)) == 1
    state.revoke(peer_id)
    assert store.inbox(first) == []


def test_automatic_answers_wake_the_owner_agent_only_when_held_or_chosen(
    tmp_path: Path, isolated_collab: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_wake(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "skipped", "via": "test"}

    replies = iter(["We use iroh.", "token ghp_" + "a" * 36, "Still iroh."])

    def fake_respond(*_args: object) -> Reply:
        return Reply(next(replies), None)

    monkeypatch.setattr("llm_bus.collab.delivery.dispatch_pending", no_wake)
    monkeypatch.setattr("llm_bus.collab.delivery.respond", fake_respond)
    store = Store(tmp_path / "bus.sqlite3")
    me = store.register("claude", "me", str(tmp_path))
    state = CollabState(isolated_collab)
    add_full_peer(state, me, mode="readonly")
    state.set_mode(state.peers()[0]["id"], grant("readonly", review_replies=False))
    daemon = Daemon(state, store)

    async def ask(uid: str) -> None:
        frame: Frame = {"type": "msg", "pair": "p", "uid": uid, "kind": "request", "body": "?"}
        assert await daemon.handle("e", frame) == {"ok": True}
        await wait_until(
            lambda: next(m for m in state.messages() if m["uid"] == uid)["state"] != "responding"
        )

    asyncio.run(ask("plain"))
    assert store.inbox(me) == []
    asyncio.run(ask("secret"))
    notices = store.inbox(me)
    assert len(notices) == 1
    assert "review" in notices[0]["body"]
    state.set_mode(
        state.peers()[0]["id"], grant("readonly", review_replies=False, wake_on_answer=True)
    )
    asyncio.run(ask("woken"))
    assert len(store.inbox(me)) == 2


def test_existing_state_gains_agent_replies_column(tmp_path: Path) -> None:
    home = tmp_path / "old"
    home.mkdir()
    with contextlib.closing(sqlite3.connect(home / "collab.sqlite3")) as db:
        db.executescript(
            "CREATE TABLE peers (id INTEGER PRIMARY KEY, pair TEXT NOT NULL UNIQUE, "
            "github_user TEXT NOT NULL, repository TEXT NOT NULL, endpoint_id TEXT NOT NULL, "
            "ticket TEXT, key_fingerprint TEXT NOT NULL, mode TEXT NOT NULL, full_until TEXT, "
            "review_replies INTEGER NOT NULL, their_grant TEXT, session TEXT, "
            "confirm_code TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT);"
            "INSERT INTO peers(pair, github_user, repository, endpoint_id, key_fingerprint, mode, "
            "review_replies, confirm_code, created_at) "
            "VALUES ('p', 'bob', '/repo', 'e', 'f', 'readonly', 1, 'c', 'now');"
        )
    peer = CollabState(home).peers()[0]
    assert (peer["agent_replies"], peer["wake_on_answer"]) == (False, False)
