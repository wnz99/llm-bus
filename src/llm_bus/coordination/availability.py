"""Durable informational local arrivals; these records confer no authority."""

from sqlite3 import Connection

from llm_bus.mailbox.writes import send_in_transaction


def record_local(
    db: Connection, record: dict[str, object], worker: str, grant_id: str
) -> int | None:
    """Caller owns the validated join transaction, including notice deduplication."""
    if (
        db.execute(
            "SELECT 1 FROM coordination_local_availability WHERE grant_id=?", (grant_id,)
        ).fetchone()
        is not None
    ):
        return None
    message = send_in_transaction(
        db,
        worker,
        str(record["coordinator"]),
        f"Worker {worker} joined coordination project {record['label']}. "
        "Availability is informational; recheck current authority before assignments.",
    )
    db.execute(
        "INSERT INTO coordination_local_availability(grant_id,message_id) VALUES (?,?)",
        (grant_id, message["id"]),
    )
    return message["id"]
