"""Batches ownership."""

from __future__ import annotations

import urllib.parse
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.policy.fields import array_values, object_fields, required_text
from llm_bus.remote import outgoing, protocol
from llm_bus.remote.constants import BATCH_CAPABILITY_SECONDS, UNSUPPORTED_BATCH
from llm_bus.remote.http import HTTP_GET, HTTP_POST, RelayError
from llm_bus.reports.protocol import parse_report
from llm_bus.reports.store import Reports

if TYPE_CHECKING:
    from llm_bus.remote.bridge import Bridge
    from llm_bus.remote.models import Envelope


def read_batch(self: Bridge, kind: str, ids: list[str]) -> dict[str, tuple[object, str | None]]:
    path = "/v1/status" if kind == "status" else "/v1/reports"
    field = "ids" if kind == "status" else "message_ids"
    until = self.unsupported_until.get(kind)
    if until is None or self.clock() >= until:
        try:
            value = self.relay.request(HTTP_POST, path + "/batch", {field: ids})
        except RelayError as error:
            if error.http_status not in UNSUPPORTED_BATCH:
                raise
            self.unsupported_until[kind] = self.clock() + timedelta(
                seconds=BATCH_CAPABILITY_SECONDS
            )
        else:
            return validate_batch(kind, ids, value)
    result: dict[str, tuple[object, str | None]] = {}
    query_field = "id" if kind == "status" else "message_id"
    for key in ids:
        try:
            result[key] = (
                self.relay.request(
                    HTTP_GET, path + "?" + urllib.parse.urlencode({query_field: key})
                ),
                None,
            )
        except BusError as error:
            code = error.code if isinstance(error, RelayError) else "unavailable"
            result[key] = (None, code)
    return result


def validate_batch(
    kind: str, ids: list[str], value: object
) -> dict[str, tuple[object, str | None]]:
    wrapper = object_fields(value)
    if set(wrapper) != {"results"}:
        raise RelayError("protocol_error")
    result: dict[str, tuple[object, str | None]] = {}
    for item in array_values(wrapper["results"]):
        data = object_fields(item)
        key = required_text(data.get("id"))
        if set(data) != {"id", "value", "error"} or key not in ids or key in result:
            raise RelayError("protocol_error")
        error = data["error"]
        if error is not None:
            details = object_fields(error)
            code = details.get("code")
            if (
                set(details) != {"code"}
                or not isinstance(code, str)
                or code not in {"not_allowed", "unavailable"}
                or data["value"] is not None
            ):
                raise RelayError("protocol_error")
            result[key] = (None, cast("str", details["code"]))
        else:
            if kind == "status":
                protocol.validate_status(key, data["value"], require_id=True)
            else:
                parse_report(data["value"], key)
            result[key] = (data["value"], None)
    if set(result) != set(ids):
        raise RelayError("protocol_error")
    return result


def poll_page(self: Bridge, kind: str, envelopes: list[Envelope]) -> int:
    if not envelopes:
        return 0
    try:
        values = read_batch(
            self, "status" if kind == "status" else "report", [e["id"] for e in envelopes]
        )
    except BusError as error:
        for envelope in envelopes:
            self.record(kind, envelope["id"], error)
        return len(envelopes)
    failures = 0
    for envelope in envelopes:
        key = envelope["id"]
        value, code = values[key]
        try:
            if code:
                raise RelayError(code)
            if kind == "status":
                if outgoing.permits_outgoing(self, envelope):
                    outgoing.apply_status(self, envelope, value)
            else:
                current = self.current_config()
                if current is not None:
                    Reports(self.store, self.config.connection).receive(envelope, value, current)
            self.record(kind, key)
        except BusError as error:
            self.record(kind, key, error)
            failures += 1
    return failures


def sync_outgoing(self: Bridge) -> tuple[int, int]:
    uploaded = failures = 0
    for page in self.remote.outgoing_pages():
        status_due: list[Envelope] = []
        for envelope, state in page:
            if not outgoing.permits_outgoing(self, envelope):
                continue
            kind = "upload" if state == "queued_local" else "status"
            if not self.remote.work_due(
                kind,
                envelope["id"],
                self.clock(),
                force=self.refresh_status and kind == "status",
            ):
                continue
            if kind == "status":
                status_due.append(envelope)
                continue
            try:
                if outgoing.upload(self, envelope):
                    uploaded += 1
                    self.record(kind, envelope["id"])
                    self.record("status", envelope["id"])
            except BusError as error:
                self.record(kind, envelope["id"], error)
                failures += 1
        status_failures = poll_page(self, "status", status_due)
        failures += status_failures
        uploaded += len(status_due) - status_failures
    return uploaded, failures
