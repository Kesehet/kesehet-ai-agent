from importlib import import_module
from typing import Any


__all__ = ["get_all_tool_details", "get_tool"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.tool_helper.main"), name)
