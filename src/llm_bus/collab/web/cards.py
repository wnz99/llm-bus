"""Owner-only local page: every grant, approval, and peer message read happens here."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from llm_bus.collab.models import (
    MAX_STATUS_SUMMARY,
    MESSAGE_FAILED,
    MESSAGE_REJECTED,
)
from llm_bus.collab.policy import effective_mode
from llm_bus.collab.web.layout import escape, mode_fields

if TYPE_CHECKING:
    from llm_bus.collab.models import Message, Peer


ATTENTION_STATES = frozenset({"held", "unread", MESSAGE_FAILED, MESSAGE_REJECTED})


def peer_card(peer: Peer) -> str:
    mode = effective_mode(peer)
    user = peer["github_user"]
    chips = [
        "<span class='chip dim'>revoked</span>"
        if mode is None
        else f"<span class={'chip warn' if mode == 'full' else 'chip'}>"
        f"their messages: {escape(mode)}</span>"
    ]
    if mode == "full":
        chips.append(f"<span class='chip warn'>until {escape(peer['full_until'])}</span>")
    if mode is not None and peer["agent_replies"]:
        chips.append("<span class='chip ok'>answers to my agent</span>")
    if mode is not None and peer["wake_on_answer"]:
        chips.append("<span class='chip dim'>wakes my agent</span>")
    if mode is not None and peer["review_replies"]:
        chips.append("<span class='chip dim'>reviewing replies</span>")
    chips.append(f"<span class='chip dim'>they allow me: {escape(peer['their_grant'])}</span>")
    revoke = (
        ""
        if mode is None
        else f"<form class=inline method=post action=peer/{peer['id']}/revoke>"
        "<button class=secondary>Revoke</button></form>"
    )
    fields = mode_fields(peer["mode"], peer)
    context_fields = (
        f"<form method=post action=peer/{peer['id']}/context>"
        "<label>Responder context <select name=responder_context>"
        + "".join(
            f"<option value={value} "
            f"{'selected' if peer['responder_context'] == value else ''}>"
            f"{label}</option>"
            for value, label in (
                ("fork", "Fork: copy session history"),
                ("summary", "Summary: fresh agent with limited status"),
            )
        )
        + "</select></label><p>Applies to restricted responders; "
        "tool permissions stay the same. "
        "Isolated mode never copies session history. Monitor and full ignore this setting.</p>"
        f"<label>Status summary (up to {MAX_STATUS_SUMMARY} characters)"
        f"<textarea name=status_summary maxlength={MAX_STATUS_SUMMARY}>"
        f"{escape(peer['status_summary'])}</textarea>"
        "</label><p>Include only information this collaborator may receive. "
        "Update manually as work changes; no conversation is summarized automatically.</p>"
        "<button>Save responder context</button></form>"
    )
    change = (
        ""
        if mode is None
        else f"<details><summary>Change permissions</summary><form method=post "
        f"action=peer/{peer['id']}/mode>{fields}"
        f"<button>Save</button></form>{context_fields}</details>"
    )
    return (
        f"<div class=card><div class=row><div class=avatar>{escape(user[:1].upper())}</div>"
        f"<div style='flex:1;min-width:200px'><div class=name>@{escape(user)}</div>"
        f"<div class=sub>{escape(Path(peer['repository']).name)} · confirm code "
        f"<code>{escape(peer['confirm_code'])}</code></div></div>{revoke}</div>"
        f"<div style='margin-top:10px'>{''.join(chips)}</div>{change}</div>"
    )


def message_card(message: Message, peer: Peer | None) -> str:
    user = f"@{peer['github_user']}" if peer else "unknown peer"
    incoming = message["direction"] == "in"
    heading = f"{'From' if incoming else 'To'} {user}"
    actions = ""
    attention = False
    if not incoming and message["state"] == "held":
        attention = True
        actions = (
            "<div class=actions>"
            f"<form class=inline method=post action=message/{message['id']}/approve>"
            "<button>Send</button></form>"
            f"<form class=inline method=post action=message/{message['id']}/reject>"
            "<button class=secondary>Discard</button></form></div>"
        )
    elif incoming and message["state"] not in {"delivered", "forwarded"}:
        attention = message["state"] == "unread"
        actions = (
            "<div class=actions>"
            f"<form class=inline method=post action=message/{message['id']}/forward>"
            "<button class=secondary>Forward to my agent</button></form></div>"
        )
    tone = "warn" if message["state"] in ATTENTION_STATES else "dim"
    detail = f" <span class=sub>{escape(message['detail'])}</span>" if message["detail"] else ""
    return (
        f"<div class='card{' attn' if attention else ''}'><div class=row>"
        f"<span class=name>{'↓' if incoming else '↑'} {escape(heading)}</span>"
        f"<span class='chip dim'>{escape(message['kind'])}</span>"
        f"<span class='chip {tone}'>{escape(message['state'].replace('_', ' '))}</span>"
        f"<span class=sub style='margin-left:auto'>#{message['id']} · "
        f"{escape(message['created_at'][:16].replace('T', ' '))}</span></div>{detail}"
        f"<pre>{escape(message['body'])}</pre>{actions}</div>"
    )
