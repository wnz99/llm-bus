"""Owner-only local page: every grant, approval, and peer message read happens here."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from llm_bus.collab import identity
from llm_bus.collab.invites import decode_invite
from llm_bus.collab.models import (
    JOIN_APPROVED,
    JOIN_AWAITING,
    JOIN_CONNECTED,
)
from llm_bus.collab.web import cards
from llm_bus.collab.web.layout import escape, mode_fields, page

if TYPE_CHECKING:
    from llm_bus.collab.state import CollabState


def who(state: CollabState) -> str:
    found = state.identity()
    return f"@{found[0]}" if found else "no identity"


def dashboard(state: CollabState) -> bytes:
    parts: list[str] = []
    if state.identity() is None:
        parts.append("<p class=warn>No identity yet. Run <code>llm-bus collab setup</code>.</p>")
    if identity.test_keys_override() is not None:
        parts.append(
            "<p class=warn>Test mode: GitHub keys are read from a local folder, so "
            "identities are not verified against GitHub.</p>"
        )
    peer_list = state.peers()
    messages = state.messages(limit=100)
    waiting = sum(
        m["state"] in {"held", "unread"}
        and m["direction"] == ("out" if m["state"] == "held" else "in")
        for m in messages
    )
    parts.append(
        "<p class=muted>Everything another person's agent sends you lands here first. "
        "Only you choose what it may cause on this machine.</p>"
    )
    parts.append("<h2>Collaborators</h2>")
    parts.extend(cards.peer_card(p) for p in peer_list)
    if not peer_list:
        parts.append(
            "<div class=empty>No collaborators yet.<br><br>"
            "<a class=button href=invite>Invite collaborator</a></div>"
        )
    peers = {p["id"]: p for p in peer_list}
    badge = f" <span class='chip warn'>{waiting} need you</span>" if waiting else ""
    parts.append(f"<h2>Messages{badge}</h2>")
    parts.extend(cards.message_card(m, peers.get(m["peer_id"])) for m in messages)
    if not messages:
        parts.append("<div class=empty>No messages yet.</div>")
    return page("Collaboration", "".join(parts), who=who(state))


def invite_form(state: CollabState, query: dict[str, str]) -> bytes:
    """Invite from the agent's link or the dashboard; defaults come from known peers."""
    latest = state.peers()[-1] if state.peers() else None
    repository = query.get("repo") or (latest["repository"] if latest else str(Path.cwd()))
    session = query.get("session") or (
        latest["session"] if latest and latest["repository"] == repository else None
    )
    body = (
        "<p class=muted>Choose who to invite and what their agent's messages may cause on "
        "this machine. You get a one-time invite to send them.</p>"
        "<form method=post action=invite class=card>"
        f"<input type=hidden name=session value='{escape(session or '')}'>"
        "<label><b>GitHub username</b><input type=text name=user required "
        "placeholder=octocat autocomplete=off></label>"
        f"<label><b>Repository on this machine</b><input type=text name=repo required "
        f"value='{escape(repository)}'></label>"
        f"{mode_fields()}<button>Create invite</button></form>"
    )
    return page("Invite a collaborator", body, who=who(state))


def join_page(state: CollabState, join_id: int) -> bytes:
    text, repository, _, status, detail = state.join(join_id)
    if status != JOIN_AWAITING:
        note = (
            f"Connected. Confirm code <code>{escape(detail)}</code>. Compare it with your "
            "collaborator if you want extra assurance."
            if status == JOIN_CONNECTED
            else f"Status: {escape(status)} {escape(detail or '')}"
        )
        refresh = "<meta http-equiv=refresh content=2>" if status == JOIN_APPROVED else ""
        return page(
            "Join",
            f"{refresh}<div class=card><p>{note}</p><a class=button href='../'>Dashboard</a></div>",
            who=who(state),
            root="../",
        )
    invite = decode_invite(text)
    body = (
        "<p class=muted>These details come from the invite and are verified only when you "
        "connect.</p><div class=card>"
        f"<p>Invite from <b>@{escape(invite['host_user'])}</b> "
        f"for <b>@{escape(invite['joiner_user'])}"
        f"</b>, repository <code>{escape(invite['repository_name'])}</code>.<br>"
        f"Their machine will let your agent: <b>{escape(invite['grant'])}</b>.</p>"
        f"<p>Your local repository: <code>{escape(repository)}</code></p>"
        f"<form method=post action={join_id}>{mode_fields()}"
        "<button>Verify and connect</button></form></div>"
    )
    return page("Join a collaboration", body, who=who(state), root="../")
