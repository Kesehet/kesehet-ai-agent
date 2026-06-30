from importlib import import_module
from typing import Any


__all__ = [
    "delete_camera_config",
    "find_person",
    "get_camera_config",
    "list_camera_configs",
    "upsert_camera_config",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.find_person.main"), name)
