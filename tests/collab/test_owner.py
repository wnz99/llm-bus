"""owner."""

from __future__ import annotations

import base64
import hashlib
import http.client
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from llm_bus.collab.models import CollabError
from llm_bus.collab.policy import effective_mode
from llm_bus.collab.responder import clean_export
from llm_bus.collab.state import CollabState
from llm_bus.collab.web import pages
from llm_bus.collab.web.http import serve_ui
from llm_bus.collab.web.owner import OwnerPage
from llm_bus.mailbox.store import Store

from .support import add_full_peer, committed_repository, fetch

if TYPE_CHECKING:
    from pathlib import Path


def test_owner_context_setting_persists_and_escapes_summary(
    tmp_path: Path, isolated_collab: Path
) -> None:
    state = CollabState(isolated_collab)
    add_full_peer(state, "claude:abc", mode="readonly")
    store = Store(tmp_path / "bus.sqlite3")
    page = OwnerPage(state, store, lambda: "ticket")
    peer = state.peers()[0]
    assert peer["responder_context"] == "fork"
    assert peer["status_summary"] == ""
    summary = "Changing API </textarea><script>alert(1)</script>"
    page.apply(
        ["peer", str(peer["id"]), "context"],
        {
            "responder_context": "summary",
            "status_summary": summary,
        },
    )
    saved = CollabState(isolated_collab).peers()[0]
    assert saved["responder_context"] == "summary"
    assert saved["status_summary"] == summary
    assert saved["mode"] == "readonly"
    card = pages.dashboard(page.state).decode()
    assert "&lt;/textarea&gt;" in card
    assert "<script>alert(1)</script>" not in card
    for context, text in (("invalid", ""), ("summary", "x" * 4001)):
        with pytest.raises(CollabError):
            state.set_responder_context(peer["id"], context, text)
    assert state.peers()[0]["status_summary"] == summary


def test_concurrent_exports_publish_complete_snapshot(tmp_path: Path) -> None:
    repository = committed_repository(tmp_path)
    barrier = threading.Barrier(4)

    def export(_index: int) -> Path:
        barrier.wait(timeout=10)
        return clean_export(tmp_path / "home", str(repository))

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(export, range(4)))
    assert len(set(paths)) == 1
    assert sorted(p.name for p in paths[0].iterdir()) == ["api.py"]
    assert list(paths[0].parent.iterdir()) == [paths[0]]


def test_owner_page_requires_token_host_and_origin(tmp_path: Path, isolated_collab: Path) -> None:
    state = CollabState(isolated_collab)
    server, port = serve_ui(state, Store(tmp_path / "bus.sqlite3"), "t" * 24, lambda: "ticket")
    try:
        base = f"http://127.0.0.1:{port}"
        assert fetch(f"{base}/{'t' * 24}/") == 200
        assert fetch(f"{base}/wrong-token-wrong-token/") == 404
        assert fetch(f"{base}/{'t' * 24}/", headers={"Host": "evil.example"}) == 404
        form = b"user=bob&mode=monitor"
        assert (
            fetch(
                f"{base}/{'t' * 24}/invite", data=form, headers={"Origin": "https://evil.example"}
            )
            == 404
        )
        add_full_peer(state, "claude:me", mode="monitor")
        unconfirmed = b"mode=full&full_minutes=5"
        assert fetch(f"{base}/{'t' * 24}/peer/1/mode", data=unconfirmed) == 400
        same_origin = {"Origin": base}
        assert fetch(f"{base}/{'t' * 24}/peer/1/mode", data=unconfirmed, headers=same_origin) == 400
        assert effective_mode(state.peers()[0]) == "monitor"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", f"/{'t' * 24}/")
        response = connection.getresponse()
        assert response.getheader("Referrer-Policy") == "same-origin"
        script = response.read().decode().split("<script>")[1].split("</script>")[0]
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        assert f"script-src 'sha256-{digest}'" in (
            response.getheader("Content-Security-Policy") or ""
        )
        connection.close()
    finally:
        server.shutdown()
