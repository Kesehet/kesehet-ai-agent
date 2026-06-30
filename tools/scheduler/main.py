from typing import Any
from uuid import uuid4

from core.db import (
    calculate_next_run,
    connect,
    dumps,
    init_db,
    row_to_schedule,
    utc_now_text,
)


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        raise ValueError("Each tool call must be an object.")

    tool_name = tool_call.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("Each tool call requires a tool_name.")

    tool_parameters = tool_call.get("tool_parameters", {})
    if tool_parameters is None:
        tool_parameters = {}
    if not isinstance(tool_parameters, dict):
        raise ValueError("tool_parameters must be an object.")

    return {
        "tool_name": tool_name,
        "tool_parameters": tool_parameters,
    }


def _normalize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise ValueError("tool_calls must be a non-empty list.")

    return [_normalize_tool_call(tool_call) for tool_call in tool_calls]


def _get_schedule(schedule_id: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM schedules WHERE id = ?",
            (schedule_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Scheduled item does not exist.")
    return row_to_schedule(row)


def schedule_tool(
    tool_calls: list[dict[str, Any]],
    run_at: str | None = None,
    repeat: str | None = None,
    timezone_name: str | None = None,
    enabled: bool = True,
    notes: str | None = None,
) -> dict[str, Any]:
    if not run_at and not repeat:
        raise ValueError("Either run_at or repeat is required.")

    normalized_tool_calls = _normalize_tool_calls(tool_calls)
    now = utc_now_text()
    schedule_id = uuid4().hex
    next_run_at = calculate_next_run(run_at, repeat, timezone_name)

    init_db()
    with connect() as db:
        db.execute(
            """
            INSERT INTO schedules (
                id, tool_calls, run_at, repeat, timezone_name, enabled,
                notes, created_at, updated_at, last_run_at, next_run_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                schedule_id,
                dumps(normalized_tool_calls),
                run_at,
                repeat,
                timezone_name,
                1 if enabled else 0,
                notes,
                now,
                now,
                next_run_at,
            ),
        )

    return get_scheduled_tool(schedule_id)


def list_scheduled_tools(include_disabled: bool = True) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        if include_disabled:
            rows = db.execute(
                "SELECT * FROM schedules ORDER BY enabled DESC, next_run_at ASC"
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT * FROM schedules
                WHERE enabled = 1
                ORDER BY next_run_at ASC
                """
            ).fetchall()
    return [row_to_schedule(row) for row in rows]


def get_scheduled_tool(schedule_id: str) -> dict[str, Any]:
    return _get_schedule(schedule_id)


def update_scheduled_tool(
    schedule_id: str,
    tool_calls: list[dict[str, Any]] | None = None,
    run_at: str | None = None,
    repeat: str | None = None,
    timezone_name: str | None = None,
    enabled: bool | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    current = _get_schedule(schedule_id)

    next_tool_calls = (
        _normalize_tool_calls(tool_calls)
        if tool_calls is not None
        else current["tool_calls"]
    )
    next_run_value = run_at if run_at is not None else current["run_at"]
    next_repeat = repeat if repeat is not None else current["repeat"]
    next_timezone = (
        timezone_name
        if timezone_name is not None
        else current["timezone_name"]
    )
    next_enabled = enabled if enabled is not None else current["enabled"]
    next_notes = notes if notes is not None else current["notes"]

    if not next_run_value and not next_repeat:
        raise ValueError("Either run_at or repeat is required.")

    next_run_at = calculate_next_run(next_run_value, next_repeat, next_timezone)
    now = utc_now_text()

    with connect() as db:
        db.execute(
            """
            UPDATE schedules
            SET tool_calls = ?, run_at = ?, repeat = ?, timezone_name = ?,
                enabled = ?, notes = ?, updated_at = ?, next_run_at = ?
            WHERE id = ?
            """,
            (
                dumps(next_tool_calls),
                next_run_value,
                next_repeat,
                next_timezone,
                1 if next_enabled else 0,
                next_notes,
                now,
                next_run_at,
                schedule_id,
            ),
        )

    return get_scheduled_tool(schedule_id)


def delete_scheduled_tool(schedule_id: str) -> dict[str, str]:
    _get_schedule(schedule_id)
    with connect() as db:
        db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return {"deleted": schedule_id}
