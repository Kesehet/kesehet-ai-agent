from importlib import import_module
from typing import Any


__all__ = [
    "append_file",
    "copy_file",
    "create_directory",
    "delete_directory",
    "delete_file",
    "get_file_info",
    "list_files",
    "move_path",
    "read_file",
    "search_files",
    "write_file",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.files.main"), name)
