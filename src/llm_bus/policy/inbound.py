"""Inbound ownership."""

from __future__ import annotations

from typing import TypedDict

from llm_bus.policy.config import NATIVE, optional_config

ADDRESS_PARTS = 3


class MessageIdentity(TypedDict):
    sender: str
    recipient: str
    repo_id: str


def receive_mode(item: MessageIdentity, scope: str, current_scope: str, connection: str) -> str:
    """Check original qualified identities and both stored/current repository scopes."""
    config = optional_config()
    if config is None or config.connection != connection or scope != current_scope:
        return "deny"
    repo_id = item["repo_id"]
    source = item["sender"].split("/")
    target = item["recipient"].split("/")
    if len(source) != ADDRESS_PARTS or len(target) != ADDRESS_PARTS:
        return "deny"
    if (
        source[1] != repo_id
        or target[1] != repo_id
        or target[0] != config.machine_id
        or not NATIVE.fullmatch(source[2])
        or not NATIVE.fullmatch(target[2])
    ):
        return "deny"
    repo = next((item for item in config.repositories if item.repo_id == repo_id), None)
    peer = config.peer(repo_id, source[0])
    if repo is None or repo.scope != scope or peer is None:
        return "deny"
    return peer.receive_messages


def permits_inbox(item: MessageIdentity, scope: str, current_scope: str, connection: str) -> bool:
    return receive_mode(item, scope, current_scope, connection) == "inbox"
