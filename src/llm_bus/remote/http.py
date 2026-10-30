"""Http ownership."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import IO, TYPE_CHECKING, cast, override

from llm_bus.errors import BusError
from llm_bus.remote.credentials import read_token

if TYPE_CHECKING:
    from http.client import HTTPMessage, HTTPResponse

    from llm_bus.policy.config import Config


CLIENT_USER_AGENT = "llm-bus/0.1"


HTTP_GET = "GET"


HTTP_POST = "POST"


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


AUTHORIZATION_FAILURES = frozenset({401, 403})


class RelayError(BusError):
    """Sanitized transport failure with machine-readable retry classification."""

    code: str
    http_status: int | None

    def __init__(self, code: str, *, http_status: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        if http_status is not None:
            message = f"Relay rejected request (HTTP {http_status})"
        elif code == "protocol_error":
            message = "Invalid relay response"
        elif code == "unsupported_reply_link":
            message = "Relay does not support reply links; linked message remains queued"
        else:
            message = "Relay unavailable; local transport state retained"
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib calls this exact public signature.
    # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
    """Never forward bearer credentials to a redirect target."""

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


class Relay:
    config: Config
    token: str

    def __init__(self, config: Config) -> None:
        self.config = config
        self.token = read_token(config.credential_file)

    def request(self, method: str, path: str, data: object = None) -> object:
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        request = urllib.request.Request(  # noqa: S310 - validated origin; no redirects
            self.config.url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": CLIENT_USER_AGENT,
            },
        )
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with cast("HTTPResponse", opener.open(request, timeout=10)) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            code = "not_allowed" if error.code in AUTHORIZATION_FAILURES else "rejected"
            raise RelayError(code, http_status=error.code) from None
        except (OSError, urllib.error.URLError, HTTPException) as error:
            raise RelayError("unavailable") from error
        if len(payload) > MAX_RESPONSE_BYTES:
            raise RelayError("protocol_error")
        try:
            return cast("object", json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RelayError("protocol_error") from error
