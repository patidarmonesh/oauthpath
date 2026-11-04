"""SQLite store — immutable guide versions, jobs, reviewed-seed import."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.schema import GuideRecord, GuideStatus, JobStatus, DifficultyInfo


ALLOWED_JOB_COLUMNS = {
    "status", "stage", "app_name", "feature", "audience", "website",
    "events", "candidates", "error_message", "error_id", "guide_id",
    "guide_version", "client_ip", "created_at", "updated_at",
}


class Store:
    """Thread-safe SQLite store for guides and jobs."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS guides (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cache_key TEXT NOT NULL DEFAULT '',
                    data JSON NOT NULL,
                    PRIMARY KEY (id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_guides_cache
                    ON guides(cache_key, created_at);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT NOT NULL DEFAULT '',
                    app_name TEXT NOT NULL DEFAULT '',
                    feature TEXT NOT NULL DEFAULT '',
                    audience TEXT NOT NULL DEFAULT '',
                    website TEXT NOT NULL DEFAULT '',
                    events JSON NOT NULL DEFAULT '[]',
                    candidates JSON NOT NULL DEFAULT '[]',
                    error_message TEXT NOT NULL DEFAULT '',
                    error_id TEXT NOT NULL DEFAULT '',
                    guide_id TEXT,
                    guide_version INTEGER,
                    client_ip TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def mark_running_jobs_interrupted(self):
        """On startup, mark queued/running jobs as interrupted."""
        with self._lock:
            conn = self._get_conn()
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE jobs SET status = ?, error_message = ?, updated_at = ? "
                    "WHERE status IN (?, ?)",
                    (JobStatus.interrupted.value,
                     "Server restarted; please retry",
                     now,
                     JobStatus.queued.value,
                     JobStatus.running.value)
                )
                conn.commit()
            finally:
                conn.close()

    # ── Guide operations ──────────────────────────────────────────────────

    def save_guide(self, guide: GuideRecord) -> int:
        """Save a new immutable version. Returns the assigned version number."""
        with self._lock:
            conn = self._get_conn()
            try:
                # Ensure link_or_artifact is not None before persistence
                if guide.guide and guide.guide.next_action and guide.guide.next_action.link_or_artifact is None:
                    guide.guide.next_action.link_or_artifact = ""

                # Get next version
                row = conn.execute(
                    "SELECT MAX(version) as max_v FROM guides WHERE id = ?",
                    (guide.id,)
                ).fetchone()
                next_version = (row["max_v"] or 0) + 1

                guide.version = next_version
                guide.created_at = datetime.now(timezone.utc).isoformat()

                conn.execute(
                    "INSERT INTO guides (id, version, created_at, status, cache_key, data) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (guide.id, guide.version, guide.created_at,
                     guide.status, guide.cache_key,
                     guide.model_dump_json())
                )
                conn.commit()
                return next_version
            finally:
                conn.close()

    @staticmethod
    def _parse_guide_data(data_str: str) -> GuideRecord:
        """Parse guide JSON with automatic narrow repair for legacy null string fields."""
        try:
            return GuideRecord.model_validate_json(data_str)
        except Exception:
            try:
                data = json.loads(data_str)
                if isinstance(data, dict):
                    na = data.get("guide", {}).get("next_action")
                    if isinstance(na, dict) and na.get("link_or_artifact") is None:
                        na["link_or_artifact"] = ""
                    return GuideRecord.model_validate(data)
            except Exception:
                pass
            raise

    def get_guide(self, guide_id: str, version: int) -> Optional[GuideRecord]:
        """Get a specific version."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT data FROM guides WHERE id = ? AND version = ?",
                (guide_id, version)
            ).fetchone()
            if row:
                return self._parse_guide_data(row["data"])
            return None
        finally:
            conn.close()

    def get_latest_guide(self, guide_id: str) -> Optional[GuideRecord]:
        """Get the latest version for a guide ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT data FROM guides WHERE id = ? ORDER BY version DESC LIMIT 1",
                (guide_id,)
            ).fetchone()
            if row:
                return self._parse_guide_data(row["data"])
            return None
        finally:
            conn.close()

    def get_latest_reviewed(self, guide_id: str) -> Optional[GuideRecord]:
        """Get the latest reviewed version (excludes retracted)."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT data FROM guides WHERE id = ? AND status = ? "
                "ORDER BY version DESC LIMIT 1",
                (guide_id, GuideStatus.reviewed.value)
            ).fetchone()
            if row:
                return self._parse_guide_data(row["data"])
            return None
        finally:
            conn.close()

    def check_cache(self, cache_key: str, max_age_hours: int) -> Optional[GuideRecord]:
        """Look for a cache hit. Reviewed guides never expire; drafts obey max_age_hours.
        Retracted guides are never returned as cache hits."""
        conn = self._get_conn()
        try:
            # Try reviewed first (no age limit - human-reviewed guides remain permanent cache hits)
            # Exclude retracted versions
            row = conn.execute(
                "SELECT data FROM guides WHERE cache_key = ? "
                "AND status = ? ORDER BY version DESC LIMIT 1",
                (cache_key, GuideStatus.reviewed.value)
            ).fetchone()
            if row:
                return self._parse_guide_data(row["data"])

            # Try non-partial, non-retracted draft within max_age_hours
            cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
            row = conn.execute(
                "SELECT data FROM guides WHERE cache_key = ? AND created_at > ? "
                "AND status != ? AND status != ? ORDER BY version DESC LIMIT 1",
                (cache_key, cutoff_iso, GuideStatus.partial.value, GuideStatus.retracted.value)
            ).fetchone()
            if row:
                return self._parse_guide_data(row["data"])

            return None
        finally:
            conn.close()

    def list_guides(self) -> list[dict]:
        """List all guides (latest non-retracted version each)."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT g.id, g.version, g.created_at, g.status, g.data
                FROM guides g
                INNER JOIN (
                    SELECT id, MAX(version) as max_v FROM guides
                    WHERE status != 'retracted'
                    GROUP BY id
                ) latest ON g.id = latest.id AND g.version = latest.max_v
                ORDER BY g.created_at DESC
            """).fetchall()
            results = []
            for row in rows:
                record = self._parse_guide_data(row["data"])
                results.append({
                    "id": row["id"],
                    "version": row["version"],
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "provider_name": record.provider.name,
                    "feature": record.scenario.feature,
                    "audience": record.scenario.audience,
                    "difficulty": (record.difficulty.get("overall").label
                        if isinstance(record.difficulty.get("overall"), DifficultyInfo)
                        else record.difficulty.get("overall", {}).get("label", "Unknown")
                        if isinstance(record.difficulty.get("overall"), dict)
                        else "Unknown"),
                    "has_reviewed": False,  # will check below
                })
            # Check for reviewed versions
            for r in results:
                rv = conn.execute(
                    "SELECT 1 FROM guides WHERE id = ? AND status = ?",
                    (r["id"], GuideStatus.reviewed.value)
                ).fetchone()
                r["has_reviewed"] = rv is not None
            return results
        finally:
            conn.close()

    def import_reviewed_seed(self, guide: GuideRecord) -> tuple[bool, str]:
        """Import a reviewed seed file idempotently without mutating audited content."""
        from app.validation import validate_reviewed_record

        # Validate audit and referential integrity per R8
        valid, err = validate_reviewed_record(guide)
        if not valid:
            return False, f"Seed integrity validation failed: {err}"

        with self._lock:
            conn = self._get_conn()
            try:
                # Check if this version already exists
                existing = conn.execute(
                    "SELECT data FROM guides WHERE id = ? AND version = ?",
                    (guide.id, guide.version)
                ).fetchone()
                if existing:
                    existing_record = GuideRecord.model_validate_json(existing["data"])
                    if existing_record.model_dump() == guide.model_dump():
                        return True, "Already imported (identical)"
                    else:
                        return False, f"Version {guide.version} already exists with different data"

                conn.execute(
                    "INSERT INTO guides (id, version, created_at, status, cache_key, data) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (guide.id, guide.version, guide.created_at,
                     guide.status, guide.cache_key,
                     guide.model_dump_json())
                )
                conn.commit()
                return True, f"Imported version {guide.version}"
            finally:
                conn.close()

    def get_all_versions(self, guide_id: str) -> list[dict]:
        """List all versions for a guide."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT version, created_at, status FROM guides WHERE id = ? ORDER BY version",
                (guide_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def retract_guide_version(self, guide_id: str, version: int, reason: str) -> bool:
        """Mark a guide version as retracted.

        Preserves the row for audit history but removes it from active publication.
        Retracted versions are excluded from check_cache, list_guides latest selection,
        and get_latest_reviewed results.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT data FROM guides WHERE id = ? AND version = ?",
                    (guide_id, version)
                ).fetchone()
                if not row:
                    return False
                data = json.loads(row["data"])
                data["status"] = GuideStatus.retracted.value
                data["retraction_reason"] = reason
                conn.execute(
                    "UPDATE guides SET status = ?, data = ? WHERE id = ? AND version = ?",
                    (GuideStatus.retracted.value, json.dumps(data), guide_id, version)
                )
                conn.commit()
                return True
            finally:
                conn.close()

    # ── Job operations ────────────────────────────────────────────────────

    def create_job(self, job_id: str, app_name: str, feature: str = "",
                   audience: str = "external customers", website: str = "",
                   client_ip: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO jobs (id, status, app_name, feature, audience, website, "
                "client_ip, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, JobStatus.queued.value, app_name, feature, audience,
                 website, client_ip, now, now)
            )
            conn.commit()
        finally:
            conn.close()

    def update_job(self, job_id: str, **kwargs) -> None:
        for k in kwargs:
            if k not in ALLOWED_JOB_COLUMNS:
                raise ValueError(f"Invalid column for job update: {k}")
        with self._lock:
            conn = self._get_conn()
            try:
                kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                values = list(kwargs.values()) + [job_id]
                conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
                conn.commit()
            finally:
                conn.close()

    def add_job_event(self, job_id: str, event: str) -> None:
        """Add a progress event (capped at 50, max 300 chars each)."""
        event = event[:300]
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT events FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row:
                    events = json.loads(row["events"])
                    events.append(event)
                    if len(events) > 50:
                        events = events[-50:]
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "UPDATE jobs SET events = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(events), now, job_id)
                    )
                    conn.commit()
            finally:
                conn.close()

    def get_job(self, job_id: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                result = dict(row)
                result["events"] = json.loads(result["events"])
                result["candidates"] = json.loads(result["candidates"])
                return result
            return None
        finally:
            conn.close()

    def count_recent_jobs(self, client_ip: str, hours: int = 1) -> int:
        """Count recent jobs from an IP for rate limiting."""
        conn = self._get_conn()
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM jobs WHERE client_ip = ? AND created_at > ?",
                (client_ip, cutoff_iso)
            ).fetchone()
            return row["cnt"]
        finally:
            conn.close()
