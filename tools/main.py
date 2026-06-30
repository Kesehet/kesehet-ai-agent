import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
MANIFEST_NAME = "manifest.json"
MAIN_FILE_NAME = "main.py"


class LazyToolCallable:
    def __init__(self, module_path: str, function_name: str) -> None:
        self.module_path = module_path
        self.function_name = function_name
        self.__name__ = function_name

    def __call__(self, **kwargs: Any) -> Any:
        module = importlib.import_module(self.module_path)
        function = getattr(module, self.function_name)
        return function(**kwargs)


def _has_test_file(tool_dir: Path) -> bool:
    return any(
        path.is_file() and path.name.startswith("test_") and path.suffix == ".py"
        for path in tool_dir.iterdir()
    )


def validate_tool_package(tool_dir: Path) -> None:
    manifest_path = tool_dir / MANIFEST_NAME
    main_path = tool_dir / MAIN_FILE_NAME

    if not main_path.is_file():
        raise ValueError(f"{tool_dir.name} is missing {MAIN_FILE_NAME}.")
    if not manifest_path.is_file():
        raise ValueError(f"{tool_dir.name} is missing {MANIFEST_NAME}.")
    if not _has_test_file(tool_dir):
        raise ValueError(f"{tool_dir.name} is missing a test_*.py file.")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{manifest_path} contains invalid JSON.") from exc

    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path} must contain a JSON object.")
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        raise ValueError(f"{manifest_path} requires a non-empty name.")
    if not isinstance(manifest.get("module"), str) or not manifest["module"]:
        raise ValueError(f"{manifest_path} requires a non-empty module.")
    if not isinstance(manifest.get("tools"), list):
        raise ValueError(f"{manifest_path} requires a tools list.")

    return manifest


def _build_tool(manifest: dict[str, Any], tool_config: dict[str, Any]) -> dict[str, Any]:
    tool = deepcopy(tool_config)
    function = tool.get("function")
    if not isinstance(function, dict):
        raise ValueError(f"{manifest['name']} tool entry is missing function schema.")

    entrypoint = tool.pop("entrypoint", function.get("name"))
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ValueError(f"{manifest['name']} tool entry requires an entrypoint.")

    function["function_object"] = LazyToolCallable(manifest["module"], entrypoint)
    return tool


def discover_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []

    for tool_dir in sorted(path for path in TOOLS_ROOT.iterdir() if path.is_dir()):
        if tool_dir.name.startswith("__"):
            continue

        validate_tool_package(tool_dir)
        manifest = _load_manifest(tool_dir / MANIFEST_NAME)
        for tool_config in manifest["tools"]:
            if not isinstance(tool_config, dict):
                raise ValueError(f"{manifest['name']} has a non-object tool entry.")
            tools.append(_build_tool(manifest, tool_config))

    return tools


ALL_TOOLS = discover_tools()


def refresh_tools() -> list[dict[str, Any]]:
    ALL_TOOLS.clear()
    ALL_TOOLS.extend(discover_tools())
    return ALL_TOOLS


def get_tool_summaries() -> list[dict[str, str]]:
    return [
        {
            "name": tool["function"]["name"],
            "category": tool.get("category", ""),
            "description": tool["function"]["description"],
        }
        for tool in ALL_TOOLS
        if tool.get("category") != "tools"
    ]


def parse_tool_names(raw_response: str) -> list[str]:
    raw_response = raw_response.strip()
    if raw_response.startswith("```"):
        lines = raw_response.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_response = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, dict):
        parsed = parsed.get("tools", [])

    if not isinstance(parsed, list):
        return []

    names: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])

    return names
