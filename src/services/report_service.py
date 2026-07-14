"""Report tracking backed by SQLite."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .sqlite_utils import sqlite_connection, sqlite_transaction


logger = logging.getLogger(__name__)

VALID_REPORT_TYPES = ("issue", "feature")
VALID_REPORT_STATUSES = ("open", "triaged", "in_progress", "done", "declined")


@dataclass(frozen=True)
class Report:
    """A bot issue or feature request submitted from Discord."""

    id: int
    guild_id: Optional[int]
    channel_id: Optional[int]
    reporter_id: int
    reporter_name: str
    report_type: str
    description: str
    status: str
    admin_notes: Optional[str]
    created_at: str
    updated_at: str


class ReportService:
    """Persists bot reports and their current status."""

    def __init__(self, db_path: str = "data/token_usage.db"):
        self.db_path = Path(db_path).expanduser()
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite_transaction(self.db_path, row_factory=sqlite3.Row) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    reporter_id INTEGER NOT NULL,
                    reporter_name TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    admin_notes TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bot_reports_status
                ON bot_reports (status, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_bot_reports_guild
                ON bot_reports (guild_id, id DESC)
                """
            )
        logger.info("Report tracking database ready at %s", self.db_path)

    def create_report(
        self,
        *,
        guild_id: Optional[int],
        channel_id: Optional[int],
        reporter_id: int,
        reporter_name: str,
        report_type: str,
        description: str,
    ) -> Report:
        """Create a new report and return the persisted row."""

        normalized_type = self._normalize_report_type(report_type)
        cleaned_description = description.strip()
        if not cleaned_description:
            raise ValueError("Report description is required")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with sqlite_transaction(self.db_path, row_factory=sqlite3.Row) as conn:
            cursor = conn.execute(
                """
                INSERT INTO bot_reports (
                    guild_id,
                    channel_id,
                    reporter_id,
                    reporter_name,
                    report_type,
                    description,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    int(reporter_id),
                    reporter_name[:100],
                    normalized_type,
                    cleaned_description,
                    now,
                    now,
                ),
            )
            report_id = int(cursor.lastrowid)

        report = self.get_report(report_id)
        if not report:
            raise RuntimeError(f"Report {report_id} was not persisted")
        return report

    def get_report(self, report_id: int) -> Optional[Report]:
        """Return one report by ID, or None if it does not exist."""

        with sqlite_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM bot_reports
                WHERE id = ?
                """,
                (int(report_id),),
            ).fetchone()
        return self._row_to_report(row) if row else None

    def list_reports(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[Report]:
        """Return recent reports, optionally filtered by status."""

        bounded_limit = min(max(int(limit), 1), 500)
        if status:
            normalized_status = self._normalize_status(status)
            query = """
                SELECT *
                FROM bot_reports
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
            """
            params = (normalized_status, bounded_limit)
        else:
            query = """
                SELECT *
                FROM bot_reports
                ORDER BY id DESC
                LIMIT ?
            """
            params = (bounded_limit,)

        with sqlite_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_report(row) for row in rows]

    def update_status(
        self,
        report_id: int,
        status: str,
        *,
        admin_notes: Optional[str] = None,
    ) -> Optional[Report]:
        """Update report status and optional notes. Returns the updated report."""

        normalized_status = self._normalize_status(status)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with sqlite_transaction(self.db_path, row_factory=sqlite3.Row) as conn:
            if admin_notes is None:
                cursor = conn.execute(
                    """
                    UPDATE bot_reports
                    SET status = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_status, now, int(report_id)),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE bot_reports
                    SET status = ?,
                        admin_notes = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_status, admin_notes.strip(), now, int(report_id)),
                )
            if cursor.rowcount == 0:
                return None

        return self.get_report(report_id)

    @staticmethod
    def _normalize_report_type(report_type: str) -> str:
        normalized = (report_type or "").strip().lower()
        if normalized not in VALID_REPORT_TYPES:
            raise ValueError(f"report_type must be one of: {', '.join(VALID_REPORT_TYPES)}")
        return normalized

    @staticmethod
    def _normalize_status(status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in VALID_REPORT_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(VALID_REPORT_STATUSES)}")
        return normalized

    @staticmethod
    def _row_to_report(row: sqlite3.Row) -> Report:
        return Report(
            id=int(row["id"]),
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            reporter_id=int(row["reporter_id"]),
            reporter_name=row["reporter_name"],
            report_type=row["report_type"],
            description=row["description"],
            status=row["status"],
            admin_notes=row["admin_notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
