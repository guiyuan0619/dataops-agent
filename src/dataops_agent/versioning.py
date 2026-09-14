from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from dataops_agent.consumer_gate import evaluate_consumer_gate
from dataops_agent.schemas import VersionRecord


class ActiveVersionConflictError(ValueError):
    """Raised when a candidate was approved against a stale active version."""


class IdempotencyConflictError(ValueError):
    """Raised when the same idempotency key is reused for different data."""


@dataclass(frozen=True)
class VersionStore:
    db_path: Path
    artifact_dir: Path

    def init_db(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                create table if not exists versions (
                    version_id text primary key,
                    dataset_name text not null,
                    path text not null,
                    checksum text not null,
                    created_at text not null,
                    parent_version_id text,
                    quality_report text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists active_versions (
                    dataset_name text primary key,
                    version_id text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists publish_requests (
                    dataset_name text not null,
                    idempotency_key text not null,
                    candidate_checksum text not null,
                    version_id text not null,
                    status text not null,
                    created_at text not null,
                    primary key (dataset_name, idempotency_key)
                )
                """
            )

    def publish(
        self,
        *,
        dataset_name: str,
        df: pd.DataFrame,
        quality_report: dict[str, Any],
        approved: bool,
        parent_version_id: str | None = None,
        idempotency_key: str | None = None,
        expected_active_version_id: str | None = None,
        require_expected_active_match: bool = False,
    ) -> VersionRecord:
        if not approved:
            raise PermissionError("Publishing requires explicit approval.")
        self.init_db()
        payload = df.to_csv(index=False)
        checksum = sha256(payload.encode("utf-8")).hexdigest()
        created_at = datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute("begin immediate")
            if idempotency_key:
                existing_request = conn.execute(
                    """
                    select * from publish_requests
                    where dataset_name = ? and idempotency_key = ?
                    """,
                    (dataset_name, idempotency_key),
                ).fetchone()
                if existing_request:
                    if str(existing_request["candidate_checksum"]) != checksum:
                        raise IdempotencyConflictError(
                            "Idempotency key was reused with different candidate data."
                        )
                    existing_version = conn.execute(
                        "select * from versions where version_id = ?",
                        (str(existing_request["version_id"]),),
                    ).fetchone()
                    if not existing_version:
                        raise RuntimeError("Idempotency record points to a missing version.")
                    return _row_to_record(existing_version)

            active = self._active_version_in_transaction(conn, dataset_name)
            current_active_version_id = active.version_id if active else None
            if (
                require_expected_active_match
                and current_active_version_id != expected_active_version_id
            ):
                raise ActiveVersionConflictError(
                    "Active version changed before publish: "
                    f"expected {expected_active_version_id or 'none'}, "
                    f"actual {current_active_version_id or 'none'}."
                )

            gate = evaluate_consumer_gate(
                dataset_name=dataset_name,
                candidate=df,
                quality_report=quality_report,
                baseline=pd.read_csv(active.path) if active else None,
            )
            if not gate["release_gate_passed"]:
                raise ValueError(f"Consumer regression gate failed: {gate['status']}.")
            version_id = f"v-{uuid4().hex[:12]}"
            path = self.artifact_dir / dataset_name / f"{version_id}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")

            conn.execute(
                """
                insert into versions (
                    version_id,
                    dataset_name,
                    path,
                    checksum,
                    created_at,
                    parent_version_id,
                    quality_report
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    dataset_name,
                    str(path),
                    checksum,
                    created_at,
                    parent_version_id,
                    json.dumps(quality_report, sort_keys=True),
                ),
            )
            conn.execute(
                """
                insert into active_versions (dataset_name, version_id)
                values (?, ?)
                on conflict(dataset_name) do update set version_id=excluded.version_id
                """,
                (dataset_name, version_id),
            )
            if idempotency_key:
                conn.execute(
                    """
                    insert into publish_requests (
                        dataset_name,
                        idempotency_key,
                        candidate_checksum,
                        version_id,
                        status,
                        created_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset_name,
                        idempotency_key,
                        checksum,
                        version_id,
                        "published",
                        created_at,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return VersionRecord(
            version_id=version_id,
            dataset_name=dataset_name,
            path=str(path),
            checksum=checksum,
            created_at=created_at,
            parent_version_id=parent_version_id,
            quality_report=quality_report,
        )

    def _active_version_in_transaction(
        self,
        conn: sqlite3.Connection,
        dataset_name: str,
    ) -> VersionRecord | None:
        row = conn.execute(
            """
            select versions.*
            from active_versions
            join versions on active_versions.version_id = versions.version_id
            where active_versions.dataset_name = ?
            """,
            (dataset_name,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def rollback(
        self,
        *,
        dataset_name: str,
        target_version_id: str,
        approved: bool,
        approver: str = "local_user",
    ) -> VersionRecord:
        if not approved:
            raise PermissionError("Rollback requires explicit approval.")
        target = self.get_version(target_version_id)
        if not target or target.dataset_name != dataset_name:
            raise ValueError("Target version does not exist for this dataset.")
        from dataops_agent.decision_memory import GovernanceDecisionMemoryStore

        memory_store = GovernanceDecisionMemoryStore(self.db_path)
        memory_store.init_db()
        with closing(self._connect()) as conn, conn:
            conn.execute("begin immediate")
            previous = self._active_version_in_transaction(conn, dataset_name)
            conn.execute(
                """
                insert into active_versions (dataset_name, version_id)
                values (?, ?)
                on conflict(dataset_name) do update set version_id=excluded.version_id
                """,
                (dataset_name, target_version_id),
            )
            if previous and previous.version_id != target_version_id:
                memory_store.invalidate_published_version_in_transaction(
                    conn,
                    dataset_name=dataset_name,
                    version_id=previous.version_id,
                    actor=approver,
                    reason=(
                        "Published version was rolled back; dependent decisions require review."
                    ),
                )
        return target

    def active_version(self, dataset_name: str) -> VersionRecord | None:
        self.init_db()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select version_id from active_versions where dataset_name = ?",
                (dataset_name,),
            ).fetchone()
        if not row:
            return None
        return self.get_version(str(row["version_id"]))

    def get_version(self, version_id: str) -> VersionRecord | None:
        self.init_db()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "select * from versions where version_id = ?",
                (version_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_versions(self, dataset_name: str) -> list[VersionRecord]:
        self.init_db()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "select * from versions where dataset_name = ? order by created_at",
                (dataset_name,),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn


def _row_to_record(row: sqlite3.Row) -> VersionRecord:
    return VersionRecord(
        version_id=str(row["version_id"]),
        dataset_name=str(row["dataset_name"]),
        path=str(row["path"]),
        checksum=str(row["checksum"]),
        created_at=str(row["created_at"]),
        parent_version_id=row["parent_version_id"],
        quality_report=json.loads(str(row["quality_report"])),
    )
