from importlib import import_module
from typing import Any


__all__ = [
    "delete_scheduled_tool",
    "get_scheduled_tool",
    "list_scheduled_tools",
    "schedule_tool",
    "update_scheduled_tool",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.scheduler.main"), name)
