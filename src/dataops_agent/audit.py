from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IntegrationAuditStore:
    db_path: Path

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists integration_events (
                    event_id integer primary key autoincrement,
                    trace_id text not null,
                    operation text not null,
                    consumer_agent text not null,
                    dataset_name text not null,
                    thread_id text,
                    status text not null,
                    created_at text not null,
                    payload_json text not null
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_integration_events_trace_id
                on integration_events(trace_id)
                """
            )
            conn.execute(
                """
                create index if not exists idx_integration_events_thread_id
                on integration_events(thread_id)
                """
            )

    def record_event(
        self,
        *,
        trace_id: str,
        operation: str,
        consumer_agent: str,
        dataset_name: str,
        status: str,
        payload: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        self.init_db()
        created_at = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                insert into integration_events (
                    trace_id,
                    operation,
                    consumer_agent,
                    dataset_name,
                    thread_id,
                    status,
                    created_at,
                    payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    operation,
                    consumer_agent,
                    dataset_name,
                    thread_id,
                    status,
                    created_at,
                    payload_json,
                ),
            )
            event_id = int(cursor.lastrowid)
        return {
            "event_id": event_id,
            "trace_id": trace_id,
            "operation": operation,
            "consumer_agent": consumer_agent,
            "dataset_name": dataset_name,
            "thread_id": thread_id,
            "status": status,
            "created_at": created_at,
            "payload": payload,
        }

    def list_events(
        self,
        *,
        trace_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.init_db()
        limit = max(1, min(limit, 200))
        where = []
        params: list[Any] = []
        if trace_id is not None:
            where.append("trace_id = ?")
            params.append(trace_id)
        if thread_id is not None:
            where.append("thread_id = ?")
            params.append(thread_id)
        where_sql = f"where {' and '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select *
                from integration_events
                {where_sql}
                order by event_id asc
                limit ?
                """,
                (*params, limit),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def build_integration_trace_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts if part is not None)
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"dataops-trace-{digest[:16]}"


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": int(row["event_id"]),
        "trace_id": str(row["trace_id"]),
        "operation": str(row["operation"]),
        "consumer_agent": str(row["consumer_agent"]),
        "dataset_name": str(row["dataset_name"]),
        "thread_id": row["thread_id"],
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "payload": json.loads(str(row["payload_json"])),
    }
