import threading
from datetime import timezone
from typing import Any

from core.db import (
    calculate_next_run,
    connect,
    create_job,
    init_db,
    row_to_job,
    row_to_schedule,
    utc_now,
    utc_now_text,
    update_job,
)


POLL_SECONDS = 5
_runner: "SchedulerRunner | None" = None
_runner_lock = threading.Lock()


class SchedulerRunner:
    def __init__(self, poll_seconds: int = POLL_SECONDS) -> None:
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name="scheduler-runner",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        init_db()
        while not self._stop.is_set():
            try:
                run_due_schedules()
            except Exception as exc:
                job_id = create_job(None, "scheduler_runner", {})
                update_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                    started_at=utc_now_text(),
                    finished_at=utc_now_text(),
                )
            self._stop.wait(self.poll_seconds)


def start_scheduler_runner() -> SchedulerRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = SchedulerRunner()
            _runner.start()
        return _runner


def _tool_registry() -> dict[str, Any]:
    from tools.main import ALL_TOOLS

    registry: dict[str, Any] = {}
    for tool in ALL_TOOLS:
        function = tool.get("function", {})
        name = function.get("name")
        function_object = function.get("function_object")
        if name and callable(function_object):
            registry[name] = function_object
    return registry


def _claim_due_schedules(limit: int = 5) -> list[dict[str, Any]]:
    now = utc_now_text()
    claimed_at = now
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """
            SELECT * FROM schedules
            WHERE enabled = 1
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()

        schedules = []
        for row in rows:
            schedule = row_to_schedule(row)
            cursor = db.execute(
                """
                UPDATE schedules
                SET next_run_at = NULL, updated_at = ?
                WHERE id = ?
                  AND enabled = 1
                  AND next_run_at = ?
                """,
                (claimed_at, schedule["id"], schedule["next_run_at"]),
            )
            if cursor.rowcount == 1:
                schedules.append(schedule)
    return schedules


def _advance_schedule(schedule: dict[str, Any]) -> None:
    now_dt = utc_now()
    now = now_dt.isoformat()
    repeat = schedule.get("repeat")
    if repeat:
        next_run_at = calculate_next_run(
            schedule.get("run_at"),
            repeat,
            schedule.get("timezone_name"),
            after=now_dt,
        )
        enabled = 1
    else:
        next_run_at = None
        enabled = 0

    with connect() as db:
        db.execute(
            """
            UPDATE schedules
            SET enabled = ?, last_run_at = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (enabled, now, next_run_at, now, schedule["id"]),
        )


def _execute_tool_call(
    schedule_id: str,
    tool_name: str,
    tool_parameters: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    job_id = create_job(schedule_id, tool_name, tool_parameters)
    update_job(job_id, status="running", started_at=utc_now_text())

    if tool_name not in registry:
        error = f"Tool '{tool_name}' is not registered."
        update_job(
            job_id,
            status="failed",
            error=error,
            finished_at=utc_now_text(),
        )
        return {"job_id": job_id, "tool_name": tool_name, "error": error}

    try:
        result = registry[tool_name](**tool_parameters)
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            error=str(exc),
            finished_at=utc_now_text(),
        )
        return {"job_id": job_id, "tool_name": tool_name, "error": str(exc)}

    update_job(
        job_id,
        status="succeeded",
        result=result,
        finished_at=utc_now_text(),
    )
    return {"job_id": job_id, "tool_name": tool_name, "result": result}


def run_due_schedules(limit: int = 5) -> list[dict[str, Any]]:
    init_db()
    registry = _tool_registry()
    runs = []
    for schedule in _claim_due_schedules(limit):
        results = []
        for tool_call in schedule.get("tool_calls", []):
            tool_name = tool_call.get("tool_name")
            tool_parameters = tool_call.get("tool_parameters") or {}
            if isinstance(tool_name, str) and isinstance(tool_parameters, dict):
                results.append(
                    _execute_tool_call(
                        schedule["id"],
                        tool_name,
                        tool_parameters,
                        registry,
                    )
                )
        _advance_schedule(schedule)
        runs.append({"schedule": schedule, "results": results})
    return runs


def get_scheduler_status() -> dict[str, Any]:
    init_db()
    now = utc_now().astimezone(timezone.utc).isoformat()
    with connect() as db:
        running_rows = db.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        recent_rows = db.execute(
            """
            SELECT * FROM jobs
            WHERE status NOT IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()
        schedule_rows = db.execute(
            """
            SELECT * FROM schedules
            WHERE enabled = 1
            ORDER BY next_run_at ASC
            LIMIT 20
            """
        ).fetchall()

    return {
        "now": now,
        "running": [row_to_job(row) for row in running_rows],
        "recent": [row_to_job(row) for row in recent_rows],
        "scheduled": [row_to_schedule(row) for row in schedule_rows],
    }

