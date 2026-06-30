import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


INTERNAL_ROOT = Path(__file__).resolve().parents[2] / "internal"
SCHEDULE_PATH = INTERNAL_ROOT / "calendar" / "scheduled.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schedule_file() -> None:
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SCHEDULE_PATH.exists():
        SCHEDULE_PATH.write_text("[]", encoding="utf-8")


def _read_scheduled() -> list[dict[str, Any]]:
    _ensure_schedule_file()
    try:
        scheduled = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Scheduled calendar file contains invalid JSON.") from exc

    if not isinstance(scheduled, list):
        raise ValueError("Scheduled calendar file must contain a JSON list.")

    return scheduled


def _write_scheduled(scheduled: list[dict[str, Any]]) -> None:
    _ensure_schedule_file()
    SCHEDULE_PATH.write_text(
        json.dumps(scheduled, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_scheduled_index(scheduled: list[dict[str, Any]], schedule_id: str) -> int:
    for index, item in enumerate(scheduled):
        if item.get("id") == schedule_id:
            return index

    raise ValueError("Scheduled item does not exist.")


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
    now = _utc_now()
    item = {
        "id": uuid4().hex,
        "tool_calls": normalized_tool_calls,
        "run_at": run_at,
        "repeat": repeat,
        "timezone": timezone_name,
        "enabled": enabled,
        "notes": notes,
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
    }

    scheduled = _read_scheduled()
    scheduled.append(item)
    _write_scheduled(scheduled)
    return item


def list_scheduled_tools(include_disabled: bool = True) -> list[dict[str, Any]]:
    scheduled = _read_scheduled()
    if include_disabled:
        return scheduled

    return [item for item in scheduled if item.get("enabled", True)]


def get_scheduled_tool(schedule_id: str) -> dict[str, Any]:
    scheduled = _read_scheduled()
    return scheduled[_find_scheduled_index(scheduled, schedule_id)]


def update_scheduled_tool(
    schedule_id: str,
    tool_calls: list[dict[str, Any]] | None = None,
    run_at: str | None = None,
    repeat: str | None = None,
    timezone_name: str | None = None,
    enabled: bool | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    scheduled = _read_scheduled()
    index = _find_scheduled_index(scheduled, schedule_id)
    item = dict(scheduled[index])

    updates = {
        "run_at": run_at,
        "repeat": repeat,
        "timezone": timezone_name,
        "enabled": enabled,
        "notes": notes,
    }
    for key, value in updates.items():
        if value is not None:
            item[key] = value

    if tool_calls is not None:
        item["tool_calls"] = _normalize_tool_calls(tool_calls)

    if not item.get("run_at") and not item.get("repeat"):
        raise ValueError("Either run_at or repeat is required.")

    item["updated_at"] = _utc_now()
    scheduled[index] = item
    _write_scheduled(scheduled)
    return item


def delete_scheduled_tool(schedule_id: str) -> dict[str, str]:
    scheduled = _read_scheduled()
    index = _find_scheduled_index(scheduled, schedule_id)
    deleted = scheduled.pop(index)
    _write_scheduled(scheduled)
    return {"deleted": deleted["id"]}
