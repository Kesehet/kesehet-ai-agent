from importlib import import_module
from typing import Any


__all__ = ["web_search"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.web.main"), name)
