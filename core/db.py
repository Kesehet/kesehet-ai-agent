import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERNAL_ROOT = PROJECT_ROOT / "internal"
DB_PATH = INTERNAL_ROOT / "agent.sqlite3"
SCHEDULE_JSON_PATH = INTERNAL_ROOT / "calendar" / "scheduled.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_text() -> str:
    return utc_now().isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    INTERNAL_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                tool_calls TEXT NOT NULL,
                run_at TEXT,
                repeat TEXT,
                timezone_name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT,
                next_run_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_schedules_due
                ON schedules(enabled, next_run_at);

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                schedule_id TEXT,
                status TEXT NOT NULL,
                tool_name TEXT,
                tool_parameters TEXT,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                ON jobs(status, created_at);

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_updated
                ON memories(updated_at);
            """
        )
    migrate_schedule_json()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tool_calls": loads(row["tool_calls"], []),
        "run_at": row["run_at"],
        "repeat": row["repeat"],
        "timezone": row["timezone_name"],
        "timezone_name": row["timezone_name"],
        "enabled": bool(row["enabled"]),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_run_at": row["last_run_at"],
        "next_run_at": row["next_run_at"],
    }


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "schedule_id": row["schedule_id"],
        "status": row["status"],
        "tool_name": row["tool_name"],
        "tool_parameters": loads(row["tool_parameters"], {}),
        "result": loads(row["result"], None),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "tags": loads(row["tags"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def parse_datetime(value: str | None, timezone_name: str | None = None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Date and time must be ISO 8601.") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name or "UTC"))

    return parsed.astimezone(timezone.utc)


def calculate_next_run(
    run_at: str | None,
    repeat: str | None,
    timezone_name: str | None = None,
    after: datetime | None = None,
) -> str | None:
    baseline = after or utc_now()
    first_run = parse_datetime(run_at, timezone_name)
    if first_run and first_run > baseline:
        return first_run.isoformat()

    if not repeat:
        return first_run.isoformat() if first_run else None

    zone = ZoneInfo(timezone_name or "UTC")
    local = baseline.astimezone(zone)
    repeat_text = repeat.strip().lower()

    if repeat_text in {"hourly", "every hour"}:
        return (baseline + timedelta(hours=1)).isoformat()
    if repeat_text in {"daily", "every day"}:
        return (local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    if repeat_text in {"weekly", "every week"}:
        return (local + timedelta(weeks=1)).astimezone(timezone.utc).isoformat()

    words = repeat_text.split()
    if len(words) >= 3 and words[0] == "every" and words[1].isdigit():
        amount = int(words[1])
        unit = words[2].rstrip("s")
        intervals = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
        }
        if unit in intervals:
            return (baseline + intervals[unit]).isoformat()

    if " at " in repeat_text:
        prefix, time_text = repeat_text.rsplit(" at ", 1)
        hour, minute = _parse_hour_minute(time_text)
        days = 7 if "week" in prefix else 1
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        while candidate <= local:
            candidate += timedelta(days=days)
        return candidate.astimezone(timezone.utc).isoformat()

    cron_next = _calculate_cron_next(repeat_text, local)
    if cron_next is not None:
        return cron_next.astimezone(timezone.utc).isoformat()

    raise ValueError("Unsupported repeat format.")


def _parse_hour_minute(value: str) -> tuple[int, int]:
    hour_text, _, minute_text = value.strip().partition(":")
    hour = int(hour_text)
    minute = int(minute_text or "0")
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must be between 00:00 and 23:59.")
    return hour, minute


def _cron_matches(field: str, value: int) -> bool:
    field = field.strip()
    if field == "*":
        return True
    return any(part.isdigit() and int(part) == value for part in field.split(","))


def _calculate_cron_next(expression: str, local_now: datetime) -> datetime | None:
    fields = expression.split()
    if len(fields) != 5:
        return None

    minute_field, hour_field, day_field, month_field, weekday_field = fields
    candidate = (local_now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60):
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            _cron_matches(minute_field, candidate.minute)
            and _cron_matches(hour_field, candidate.hour)
            and _cron_matches(day_field, candidate.day)
            and _cron_matches(month_field, candidate.month)
            and _cron_matches(weekday_field, cron_weekday)
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def migrate_schedule_json() -> None:
    if not SCHEDULE_JSON_PATH.is_file():
        return

    try:
        scheduled = json.loads(SCHEDULE_JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    if not isinstance(scheduled, list):
        return

    with connect() as db:
        for item in scheduled:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            exists = db.execute(
                "SELECT 1 FROM schedules WHERE id = ?",
                (item["id"],),
            ).fetchone()
            if exists:
                continue
            now = utc_now_text()
            timezone_name = item.get("timezone") or item.get("timezone_name")
            enabled = 1 if item.get("enabled", True) else 0
            notes = item.get("notes")
            try:
                next_run_at = calculate_next_run(
                    item.get("run_at"),
                    item.get("repeat"),
                    timezone_name,
                )
            except ValueError as exc:
                next_run_at = None
                enabled = 0
                notes = (
                    f"{notes} | Migration disabled schedule: {exc}"
                    if notes
                    else f"Migration disabled schedule: {exc}"
                )
            db.execute(
                """
                INSERT INTO schedules (
                    id, tool_calls, run_at, repeat, timezone_name, enabled,
                    notes, created_at, updated_at, last_run_at, next_run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    dumps(item.get("tool_calls", [])),
                    item.get("run_at"),
                    item.get("repeat"),
                    timezone_name,
                    enabled,
                    notes,
                    item.get("created_at") or now,
                    item.get("updated_at") or now,
                    item.get("last_run_at"),
                    next_run_at,
                ),
            )


def create_job(schedule_id: str | None, tool_name: str | None, tool_parameters: dict[str, Any] | None) -> str:
    job_id = uuid4().hex
    now = utc_now_text()
    with connect() as db:
        db.execute(
            """
            INSERT INTO jobs (
                id, schedule_id, status, tool_name, tool_parameters, created_at
            ) VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (job_id, schedule_id, tool_name, dumps(tool_parameters or {}), now),
        )
    return job_id


def update_job(job_id: str, **updates: Any) -> None:
    if not updates:
        return
    fields = []
    values = []
    for key, value in updates.items():
        fields.append(f"{key} = ?")
        values.append(dumps(value) if key in {"result", "tool_parameters"} else value)
    values.append(job_id)
    with connect() as db:
        db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)


def list_jobs(statuses: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = db.execute(
                f"""
                SELECT * FROM jobs
                WHERE status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*statuses, limit],
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [row_to_job(row) for row in rows]

