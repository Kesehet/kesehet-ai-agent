from importlib import import_module
from typing import Any


__all__ = ["add_numbers", "subtract_numbers"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("tools.maths.main"), name)
