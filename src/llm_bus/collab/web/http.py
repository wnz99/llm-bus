"""Owner-only local page: every grant, approval, and peer message read happens here."""

from __future__ import annotations

import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, ClassVar, cast
from urllib.parse import parse_qs, urlsplit

from llm_bus.collab.models import (
    CollabError,
)
from llm_bus.collab.web import pages
from llm_bus.collab.web.layout import SCRIPT_HASH, escape, page
from llm_bus.collab.web.owner import OwnerPage

if TYPE_CHECKING:
    from collections.abc import Callable

    from llm_bus.collab.state import CollabState
    from llm_bus.mailbox.store import Store


MAX_FORM_BYTES = 16 * 1024


class _Handler(BaseHTTPRequestHandler):
    owner: ClassVar[OwnerPage]
    token: ClassVar[str]
    port: ClassVar[int]

    # pylint: disable-next=redefined-builtin
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002 - stdlib API
        return

    def _route(self) -> list[str] | None:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.headers.get("Host") not in hosts:
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin.removeprefix("http://") not in hosts:
            return None
        parts = urlsplit(self.path).path.strip("/").split("/")
        if not parts or not hmac.compare_digest(parts[0], self.token):
            return None
        return parts[1:]

    def _send(self, status: int, body: bytes, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{SCRIPT_HASH}'; "
            "form-action 'self'; "
            "frame-ancestors 'none'",
        )
        # no-referrer would make browsers send Origin: null on form posts.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        route = self._route()
        if route is None:
            self._send(404, b"Not found")
            return
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        try:
            match route:
                case [] | [""]:
                    body = pages.dashboard(self.owner.state)
                case ["invite"]:
                    body = pages.invite_form(self.owner.state, query)
                case ["join", number] if number.isdigit():
                    body = pages.join_page(self.owner.state, int(number))
                case _:
                    self._send(404, b"Not found")
                    return
        except CollabError as exc:
            body = page("Problem", f"<p class=warn>{escape(exc)}</p>", root=f"/{self.token}/")
        self._send(200, body)

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        route = self._route()
        length = int(self.headers.get("Content-Length") or 0)
        if route is None or length > MAX_FORM_BYTES:
            self._send(404, b"Not found")
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        base = f"/{self.token}/"
        try:
            if route == ["invite"]:
                self._send(200, self.owner.create_invite(form))
                return
            self.owner.apply(route, form)
        except (CollabError, ValueError) as exc:
            problem = f"<p class=warn>{escape(exc)}</p><a class=button href={base}>Back</a>"
            self._send(400, page("Problem", problem, root=base))
            return
        target = f"{base}join/{route[1]}" if route[0] == "join" else base
        self._send(303, b"", target)


def serve_ui(
    state: CollabState,
    store: Store,
    token: str,
    ticket: Callable[[], str],
    preferred_port: int = 0,
) -> tuple[ThreadingHTTPServer, int]:
    """Serve the owner page on loopback only; the token is the capability."""
    handler = type("OwnerHandler", (_Handler,), {"owner": OwnerPage(state, store, ticket)})
    handler.token = token
    try:
        server = ThreadingHTTPServer(("127.0.0.1", preferred_port), handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.port = cast("tuple[str, int]", server.server_address)[1]
    port = handler.port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port
