from typing import Any


def get_tool(tool_name: str) -> dict[str, Any]:
    from tools.main import ALL_TOOLS

    return next(tool for tool in ALL_TOOLS if tool["function"]["name"] == tool_name)


def get_all_tool_details() -> list[dict[str, str]]:
    from tools.main import ALL_TOOLS

    return [{"tool_name": tool["function"]["name"], "tool_description": tool["function"]["description"], } for tool in ALL_TOOLS if tool["category"] != "tools"]



