"""Approval-backed governance decision memory for semantic enum repairs."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from dataops_agent.rules import (
    DATASET_ALIASES,
    DATASET_SCHEMA_VERSIONS,
    ENUM_RULES_BY_DATASET,
    REQUIRED_COLUMNS_BY_DATASET,
)

ACTIVE = "active"
REVOKED = "revoked"
INVALIDATED = "invalidated"
EXPIRED = "expired"


@dataclass(frozen=True)
class GovernanceDecisionMemoryStore:
    db_path: Path

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists governance_decision_memory (
                    decision_id text primary key,
                    event_key text not null unique,
                    dataset_name text not null,
                    contract_version text not null,
                    field text not null,
                    source_value text not null,
                    target_value text not null,
                    outcome text not null,
                    status text not null,
                    provenance text not null,
                    thread_id text not null,
                    approver text not null,
                    reason text not null,
                    plan_checksum text not null,
                    published_version_id text,
                    created_at text not null,
                    updated_at text not null,
                    expires_at text,
                    integrity_digest text not null,
                    recall_count integer not null default 0,
                    last_recalled_at text
                )
                """
            )
            conn.execute(
                """
                create index if not exists idx_governance_memory_scope
                on governance_decision_memory (
                    dataset_name, contract_version, field, source_value, status
                )
                """
            )
            conn.execute(
                """
                create table if not exists governance_decision_memory_events (
                    event_id text primary key,
                    decision_id text not null,
                    event_type text not null,
                    actor text not null,
                    reason text not null,
                    created_at text not null
                )
                """
            )

    def record_approved_mappings(
        self,
        *,
        dataset_name: str,
        contract_version: str,
        mappings: list[dict[str, Any]],
        thread_id: str,
        approver: str,
        plan_checksum: str,
        published_version_id: str,
    ) -> list[dict[str, Any]]:
        dataset = dataset_key(dataset_name)
        self._validate_contract(dataset, contract_version)
        actor = _human_actor(approver)
        self.init_db()
        now = _utc_now()
        stored: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute("begin immediate")
            version = conn.execute(
                """
                select v.dataset_name, a.version_id as active_version_id
                from versions v
                left join active_versions a on a.dataset_name = v.dataset_name
                where v.version_id = ?
                """,
                (published_version_id,),
            ).fetchone()
            if not version or str(version["dataset_name"]) != dataset:
                raise ValueError(
                    "Approved memory requires a published version in the same dataset."
                )
            if str(version["active_version_id"] or "") != published_version_id:
                raise ValueError(
                    "Approved memory requires the published version to remain active."
                )
            for mapping in _unique_mappings(mappings):
                self._validate_mapping(dataset, mapping)
                event_key = _event_key(
                    thread_id, "approved", plan_checksum, mapping["field"],
                    mapping["source_value"], mapping["target_value"],
                )
                existing = conn.execute(
                    "select * from governance_decision_memory where event_key = ?",
                    (event_key,),
                ).fetchone()
                if existing:
                    stored.append(_public_row(existing))
                    continue
                self._invalidate_conflicts(
                    conn,
                    dataset=dataset,
                    contract_version=contract_version,
                    field=mapping["field"],
                    source_value=mapping["source_value"],
                    target_value=mapping["target_value"],
                    outcome="approved",
                    actor=actor,
                    now=now,
                )
                row = self._insert(
                    conn,
                    event_key=event_key,
                    dataset=dataset,
                    contract_version=contract_version,
                    mapping=mapping,
                    outcome="approved",
                    provenance="published_governance_run",
                    thread_id=thread_id,
                    approver=actor,
                    reason="Approved repair passed quality, consumer, and version publish gates.",
                    plan_checksum=plan_checksum,
                    published_version_id=published_version_id,
                    now=now,
                )
                stored.append(row)
            conn.commit()
        return stored

    def record_rejected_mappings(
        self,
        *,
        dataset_name: str,
        contract_version: str,
        mappings: list[dict[str, Any]],
        thread_id: str,
        approver: str,
        reason: str,
        plan_checksum: str,
    ) -> list[dict[str, Any]]:
        dataset = dataset_key(dataset_name)
        self._validate_contract(dataset, contract_version)
        actor = _human_actor(approver)
        explanation = _human_reason(reason)
        self.init_db()
        now = _utc_now()
        stored: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute("begin immediate")
            for mapping in _unique_mappings(mappings):
                self._validate_mapping(dataset, mapping)
                event_key = _event_key(
                    thread_id, "rejected", plan_checksum, mapping["field"],
                    mapping["source_value"], mapping["target_value"],
                )
                existing = conn.execute(
                    "select * from governance_decision_memory where event_key = ?",
                    (event_key,),
                ).fetchone()
                if existing:
                    stored.append(_public_row(existing))
                    continue
                self._invalidate_conflicts(
                    conn,
                    dataset=dataset,
                    contract_version=contract_version,
                    field=mapping["field"],
                    source_value=mapping["source_value"],
                    target_value=mapping["target_value"],
                    outcome="rejected",
                    actor=actor,
                    now=now,
                )
                row = self._insert(
                    conn,
                    event_key=event_key,
                    dataset=dataset,
                    contract_version=contract_version,
                    mapping=mapping,
                    outcome="rejected",
                    provenance="explicit_human_rejection",
                    thread_id=thread_id,
                    approver=actor,
                    reason=explanation,
                    plan_checksum=plan_checksum,
                    published_version_id=None,
                    now=now,
                )
                stored.append(row)
            conn.commit()
        return stored

    def recall(
        self,
        *,
        dataset_name: str,
        contract_version: str,
        groups: Iterable[tuple[str, str]],
    ) -> dict[str, Any]:
        dataset = dataset_key(dataset_name)
        self._validate_contract(dataset, contract_version)
        requested = set(groups)
        allowed = ENUM_RULES_BY_DATASET[dataset]
        self.init_db()
        now = _utc_now()
        approved: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        ignored = {"expired": 0, "invalid_integrity": 0, "invalid_provenance": 0,
                   "outside_scope": 0}
        recalled_ids: list[str] = []
        with self._connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select * from governance_decision_memory
                where dataset_name = ? and contract_version = ? and status = ?
                """,
                (dataset, contract_version, ACTIVE),
            ).fetchall()
            for row in rows:
                key = (str(row["field"]), str(row["source_value"]))
                if key not in requested:
                    ignored["outside_scope"] += 1
                    continue
                if row["expires_at"] and str(row["expires_at"]) <= now:
                    self._transition(conn, row, EXPIRED, "system", "decision_expired", now)
                    ignored["expired"] += 1
                    continue
                if not _valid_integrity(row):
                    self._transition(
                        conn, row, INVALIDATED, "system", "integrity_check_failed", now
                    )
                    ignored["invalid_integrity"] += 1
                    continue
                field = str(row["field"])
                target = str(row["target_value"])
                if field not in allowed or target not in allowed[field]:
                    self._transition(
                        conn, row, INVALIDATED, "system", "current_contract_rejected_target", now
                    )
                    ignored["invalid_provenance"] += 1
                    continue
                if row["outcome"] == "approved":
                    version = conn.execute(
                        "select dataset_name from versions where version_id = ?",
                        (row["published_version_id"],),
                    ).fetchone()
                    if not version or str(version["dataset_name"]) != dataset:
                        self._transition(
                            conn, row, INVALIDATED, "system", "published_version_missing", now
                        )
                        ignored["invalid_provenance"] += 1
                        continue
                    approved.append(_public_row(row))
                elif row["outcome"] == "rejected" and str(row["reason"]).strip():
                    rejected.append(_public_row(row))
                else:
                    self._transition(
                        conn, row, INVALIDATED, "system", "unsupported_memory_outcome", now
                    )
                    ignored["invalid_provenance"] += 1
                    continue
                recalled_ids.append(str(row["decision_id"]))
            if recalled_ids:
                placeholders = ",".join("?" for _ in recalled_ids)
                conn.execute(
                    f"""
                    update governance_decision_memory
                    set recall_count = recall_count + 1, last_recalled_at = ?
                    where decision_id in ({placeholders})
                    """,
                    (now, *recalled_ids),
                )
            conn.commit()
        return {
            "dataset_name": dataset,
            "contract_version": contract_version,
            "approved": approved,
            "rejected": rejected,
            "recalled_count": len(recalled_ids),
            "ignored": ignored,
        }

    def list_decisions(
        self,
        *,
        dataset_name: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.init_db()
        clauses: list[str] = []
        params: list[Any] = []
        if dataset_name:
            clauses.append("dataset_name = ?")
            params.append(dataset_key(dataset_name))
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select * from governance_decision_memory
                {where} order by created_at desc limit ?
                """,
                (*params, max(1, min(limit, 500))),
            ).fetchall()
        return [_public_row(row) for row in rows]

    def list_events(
        self, *, decision_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self.init_db()
        where = "where decision_id = ?" if decision_id else ""
        params: tuple[Any, ...] = (decision_id,) if decision_id else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select * from governance_decision_memory_events
                {where} order by created_at desc limit ?
                """,
                (*params, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, *, decision_id: str, actor: str, reason: str) -> dict[str, Any]:
        return self._manual_transition(
            decision_id=decision_id, status=REVOKED, actor=actor, reason=reason
        )

    def invalidate_contract(
        self, *, dataset_name: str, contract_version: str, actor: str, reason: str
    ) -> int:
        dataset = dataset_key(dataset_name)
        return self._bulk_transition(
            "dataset_name = ? and contract_version = ? and status = ?",
            (dataset, contract_version, ACTIVE),
            status=INVALIDATED,
            actor=actor,
            reason=reason,
        )

    def invalidate_published_version(
        self, *, dataset_name: str, version_id: str, actor: str, reason: str
    ) -> int:
        dataset = dataset_key(dataset_name)
        human = _human_actor(actor)
        explanation = _human_reason(reason)
        self.init_db()
        with self._connect() as conn:
            conn.execute("begin immediate")
            count = self.invalidate_published_version_in_transaction(
                conn,
                dataset_name=dataset,
                version_id=version_id,
                actor=human,
                reason=explanation,
            )
            conn.commit()
        return count

    def invalidate_published_version_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        dataset_name: str,
        version_id: str,
        actor: str,
        reason: str,
    ) -> int:
        dataset = dataset_key(dataset_name)
        human = _human_actor(actor)
        explanation = _human_reason(reason)
        now = _utc_now()
        rows = conn.execute(
            """
            select * from governance_decision_memory
            where dataset_name = ? and published_version_id = ? and status = ?
            """,
            (dataset, version_id, ACTIVE),
        ).fetchall()
        for row in rows:
            self._transition(conn, row, INVALIDATED, human, explanation, now)
        return len(rows)

    def _manual_transition(
        self, *, decision_id: str, status: str, actor: str, reason: str
    ) -> dict[str, Any]:
        human = _human_actor(actor)
        explanation = _human_reason(reason)
        self.init_db()
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from governance_decision_memory where decision_id = ?",
                (decision_id,),
            ).fetchone()
            if not row:
                raise ValueError("Decision memory does not exist.")
            if row["status"] == ACTIVE:
                self._transition(conn, row, status, human, explanation, now)
            conn.commit()
            updated = conn.execute(
                "select * from governance_decision_memory where decision_id = ?",
                (decision_id,),
            ).fetchone()
        return _public_row(updated)

    def _bulk_transition(
        self,
        where: str,
        params: tuple[Any, ...],
        *,
        status: str,
        actor: str,
        reason: str,
    ) -> int:
        human = _human_actor(actor)
        explanation = _human_reason(reason)
        self.init_db()
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("begin immediate")
            rows = conn.execute(
                f"select * from governance_decision_memory where {where}", params
            ).fetchall()
            for row in rows:
                self._transition(conn, row, status, human, explanation, now)
            conn.commit()
        return len(rows)

    def _insert(
        self,
        conn: sqlite3.Connection,
        *,
        event_key: str,
        dataset: str,
        contract_version: str,
        mapping: dict[str, str],
        outcome: str,
        provenance: str,
        thread_id: str,
        approver: str,
        reason: str,
        plan_checksum: str,
        published_version_id: str | None,
        now: str,
    ) -> dict[str, Any]:
        decision_id = f"gdm-{uuid4().hex}"
        values = {
            "decision_id": decision_id,
            "event_key": event_key,
            "dataset_name": dataset,
            "contract_version": contract_version,
            "field": mapping["field"],
            "source_value": mapping["source_value"],
            "target_value": mapping["target_value"],
            "outcome": outcome,
            "status": ACTIVE,
            "provenance": provenance,
            "thread_id": thread_id,
            "approver": approver,
            "reason": reason,
            "plan_checksum": plan_checksum,
            "published_version_id": published_version_id,
            "created_at": now,
            "updated_at": now,
            "expires_at": None,
        }
        digest = _integrity_digest(values)
        conn.execute(
            """
            insert into governance_decision_memory (
                decision_id, event_key, dataset_name, contract_version, field,
                source_value, target_value, outcome, status, provenance, thread_id,
                approver, reason, plan_checksum, published_version_id, created_at,
                updated_at, expires_at, integrity_digest
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id, event_key, dataset, contract_version, mapping["field"],
                mapping["source_value"], mapping["target_value"], outcome, ACTIVE,
                provenance, thread_id, approver, reason, plan_checksum,
                published_version_id, now, now, None, digest,
            ),
        )
        self._event(conn, decision_id, f"recorded_{outcome}", approver, reason, now)
        row = conn.execute(
            "select * from governance_decision_memory where decision_id = ?",
            (decision_id,),
        ).fetchone()
        return _public_row(row)

    def _invalidate_conflicts(
        self,
        conn: sqlite3.Connection,
        *,
        dataset: str,
        contract_version: str,
        field: str,
        source_value: str,
        target_value: str,
        outcome: str,
        actor: str,
        now: str,
    ) -> None:
        rows = conn.execute(
            """
            select * from governance_decision_memory
            where dataset_name = ? and contract_version = ? and field = ?
              and source_value = ? and status = ?
            """,
            (dataset, contract_version, field, source_value, ACTIVE),
        ).fetchall()
        for row in rows:
            same_target = str(row["target_value"]) == target_value
            if outcome == "approved":
                superseded = row["outcome"] == "approved" or same_target
            else:
                superseded = same_target
            if superseded:
                self._transition(
                    conn, row, INVALIDATED, actor, "superseded_by_new_human_decision", now
                )

    def _transition(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        status: str,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        values = dict(row)
        values["status"] = status
        values["updated_at"] = now
        digest = _integrity_digest(values)
        conn.execute(
            """
            update governance_decision_memory
            set status = ?, updated_at = ?, integrity_digest = ?
            where decision_id = ?
            """,
            (status, now, digest, row["decision_id"]),
        )
        self._event(conn, str(row["decision_id"]), status, actor, reason, now)

    def _event(
        self,
        conn: sqlite3.Connection,
        decision_id: str,
        event_type: str,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            insert into governance_decision_memory_events (
                event_id, decision_id, event_type, actor, reason, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (f"gdme-{uuid4().hex}", decision_id, event_type, actor, reason, now),
        )

    def _validate_contract(self, dataset: str, contract_version: str) -> None:
        current = DATASET_SCHEMA_VERSIONS[dataset]
        if contract_version != current:
            raise ValueError(
                f"Decision contract {contract_version} does not match current contract {current}."
            )

    def _validate_mapping(self, dataset: str, mapping: dict[str, str]) -> None:
        field = str(mapping.get("field", ""))
        source = str(mapping.get("source_value", ""))
        target = str(mapping.get("target_value", ""))
        allowed = ENUM_RULES_BY_DATASET[dataset]
        if field not in allowed or target not in allowed[field]:
            raise ValueError("Decision mapping is outside the current enum contract.")
        if not _safe_decision_value(source) or not _safe_decision_value(target):
            raise ValueError("Decision mapping contains unsafe or oversized content.")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _unique_mappings(mappings: list[dict[str, Any]]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in mappings:
        mapping = {
            "field": str(item.get("field", "")),
            "source_value": str(item.get("source_value", "")),
            "target_value": str(item.get("target_value", "")),
        }
        unique[(mapping["field"], mapping["source_value"], mapping["target_value"])] = mapping
    return list(unique.values())


def dataset_key(dataset_name: str) -> str:
    dataset = DATASET_ALIASES.get(dataset_name, dataset_name)
    if dataset not in REQUIRED_COLUMNS_BY_DATASET:
        raise ValueError(f"Unsupported dataset_name: {dataset_name}")
    return dataset


def _human_actor(value: str) -> str:
    actor = value.strip()
    if not actor or actor == "system" or len(actor) > 120:
        raise ValueError("Decision memory requires an identified human actor.")
    return actor


def _human_reason(value: str) -> str:
    reason = value.strip()
    if not 3 <= len(reason) <= 500 or any(ord(char) < 32 for char in reason):
        raise ValueError("A lifecycle transition requires a bounded explicit reason.")
    if re.search(
        r"(?:sk-[\w-]{8,}|Bearer\s+|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|\d{11,})",
        reason,
    ):
        raise ValueError("Lifecycle reasons must not contain credentials or personal identifiers.")
    return reason


def _safe_decision_value(value: str) -> bool:
    if not 0 < len(value) <= 120 or any(ord(char) < 32 for char in value):
        return False
    return not re.search(
        r"(?:sk-[\w-]{8,}|Bearer\s+|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|\d{11,})",
        value,
    )


def _event_key(*parts: str) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _integrity_digest(values: dict[str, Any] | sqlite3.Row) -> str:
    keys = (
        "decision_id", "event_key", "dataset_name", "contract_version", "field",
        "source_value", "target_value", "outcome", "status", "provenance", "thread_id",
        "approver", "reason", "plan_checksum", "published_version_id", "created_at",
        "updated_at", "expires_at",
    )
    payload = {key: values[key] for key in keys}
    return sha256(
        ("dataops-governance-decision-memory-v1:" + json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )).encode()
    ).hexdigest()


def _valid_integrity(row: sqlite3.Row) -> bool:
    return str(row["integrity_digest"]) == _integrity_digest(row)


def _public_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "decision_id": str(row["decision_id"]),
        "dataset_name": str(row["dataset_name"]),
        "contract_version": str(row["contract_version"]),
        "field": str(row["field"]),
        "source_value": str(row["source_value"]),
        "target_value": str(row["target_value"]),
        "outcome": str(row["outcome"]),
        "status": str(row["status"]),
        "provenance": str(row["provenance"]),
        "thread_id": str(row["thread_id"]),
        "approver": str(row["approver"]),
        "reason": str(row["reason"]),
        "plan_checksum": str(row["plan_checksum"]),
        "published_version_id": row["published_version_id"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "expires_at": row["expires_at"],
        "recall_count": int(row["recall_count"]),
        "last_recalled_at": row["last_recalled_at"],
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
