import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from config import get_config
from tools.main import ALL_TOOLS, get_tool_summaries, parse_tool_names
from tools.memory.main import search_memory

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

import ollama


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST")
DEFAULT_OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
DEFAULT_OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE")
DEFAULT_OLLAMA_THINK = env_bool("OLLAMA_THINK", True)
OLLAMA_CLIENT = ollama.Client(
    host=DEFAULT_OLLAMA_HOST,
    headers=(
        {"Authorization": f"Bearer {DEFAULT_OLLAMA_API_KEY}"}
        if DEFAULT_OLLAMA_API_KEY
        else None
    ),
)


def select_tools_for_prompt(
    prompt: str,
    limit: int = 20,
    messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
    tool_summaries = get_tool_summaries()
    if not tool_summaries:
        return []

    def format_tool_summaries(max_description_length: int = 110) -> str:
        lines = []
        for tool in tool_summaries:
            description = tool["description"].replace("\n", " ").strip()
            if len(description) > max_description_length:
                description = f"{description[:max_description_length - 3].rstrip()}..."

            lines.append(
                f"- name: {tool['name']}\n"
                f"  category: {tool['category']}\n"
                f"  use: {description}"
            )

        return "\n".join(lines)
    raw_selection = ollama_call(
        f"""
        You are selecting tools for an AI agent.
        Return up to {limit} tool names, most useful first.

        Important selection guidance:
        - If the user asks to save, create, write, or overwrite a text file, include write_file.
        - If the user asks to verify a saved file, include read_file or get_file_info.
        - If the user asks for current web information or to research, include web_search.
        - If the user asks to create, diagnose, fix, rebuild, or repair AI tools, include the matching tool_development tool.
        - If the user asks to add, update, list, view, or delete CCTV/property camera settings, include the matching camera config tool.
        - If the user asks to find or track people in property camera footage, include find_person.
        - If the user asks you to remember, recall, store knowledge, or use persistent memory, include memory tools.
        - If the user asks to schedule, list, update, or delete future tool runs, include scheduler tools.

        Return only valid JSON in this exact format:
        ["tool_name_1", "tool_name_2"]

        User prompt: {prompt}
        Available tools:
        {format_tool_summaries()}
        """,
        messages=messages,
        think=DEFAULT_OLLAMA_THINK,
        options={"temperature": 0},
    )

    selected_names = set(parse_tool_names(raw_selection))
    return [
        tool for tool in ALL_TOOLS
        if tool["function"]["name"] in selected_names
    ][:limit]


def select_tasks_for_prompt(prompt: str, limit: int = 20) -> list[dict[str, Any]]:
    def looks_multi_step() -> bool:
        lowered = prompt.lower()
        return any(
            marker in lowered
            for marker in [
                " and then ",
                " then ",
                " after that ",
                " finally ",
                "; ",
            ]
        )

    def fallback_tasks() -> list[dict[str, Any]]:
        separators = [
            " and then ",
            " then ",
            ". ",
            "; ",
        ]
        parts = [prompt.strip()]

        for separator in separators:
            if separator in prompt.lower():
                lowered = prompt.lower()
                split_points: list[int] = []
                start = 0
                while True:
                    index = lowered.find(separator, start)
                    if index == -1:
                        break
                    split_points.append(index)
                    start = index + len(separator)

                if split_points:
                    parts = []
                    previous = 0
                    for index in split_points:
                        part = prompt[previous:index].strip(" .;")
                        if part:
                            parts.append(part)
                        previous = index + len(separator)
                    final_part = prompt[previous:].strip(" .;")
                    if final_part:
                        parts.append(final_part)
                    break

        if len(parts) == 1:
            parts = [prompt.strip()]

        tasks: list[dict[str, Any]] = []
        max_actions = max(1, limit // 2)
        for index, part in enumerate(parts[:max_actions], start=1):
            action_id = f"t{index}"
            tasks.append({
                "id": action_id,
                "type": "action",
                "description": part,
            })
            tasks.append({
                "id": f"v{index}",
                "type": "validation",
                "description": f"Validate that {action_id} was completed correctly.",
                "validates": action_id,
            })

        return tasks[:limit]

    def parse_tasks(raw_response: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return []

        if isinstance(parsed, dict):
            parsed = parsed.get("tasks", [])

        if not isinstance(parsed, list):
            return []

        parsed_tasks: list[dict[str, Any]] = []
        for index, item in enumerate(parsed, start=1):
            if isinstance(item, str):
                parsed_tasks.append({
                    "id": f"task_{index}",
                    "type": "action",
                    "description": item,
                })
            elif isinstance(item, dict) and isinstance(item.get("description"), str):
                parsed_tasks.append(item)

        return parsed_tasks

    def normalize_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        action_ids: list[str] = []

        for task in tasks:
            task_type = task.get("type", "action")
            description = task.get("description", "").strip()
            if not description:
                continue

            if task_type == "validation":
                validates = task.get("validates")
                if not validates:
                    validates = action_ids[-1] if action_ids else "t1"

                normalized.append({
                    "id": task.get("id") or f"v{len(normalized) + 1}",
                    "type": "validation",
                    "description": description,
                    "validates": validates,
                })
                continue

            action_id = task.get("id") or f"t{len(action_ids) + 1}"
            action_ids.append(action_id)
            normalized.append({
                "id": action_id,
                "type": "action",
                "description": description,
            })

        existing_validations = {
            task.get("validates")
            for task in normalized
            if task.get("type") == "validation"
        }
        for action_id in action_ids:
            if action_id in existing_validations:
                continue

            normalized.append({
                "id": f"v_{action_id}",
                "type": "validation",
                "description": (
                    "Validate that the result of "
                    f"{action_id} is correct and useful."
                ),
                "validates": action_id,
            })

        return normalized[:limit]

    max_actions = max(1, limit // 2)
    response = OLLAMA_CLIENT.generate(
        model=DEFAULT_OLLAMA_MODEL,
        prompt=(
            "Break the user request into an execution plan for an AI agent.\n"
            f"Create atleast {max_actions} action tasks when the request has "
            "multiple meaningful steps. Do not collapse research, analysis, "
            "file edits, tool use, and final response into one task when they "
            "are distinct steps.\n"
            "After each action task, add one validation task that checks that "
            "specific action.\n"
            "Use compact, concrete descriptions. Preserve the user's intent.\n"
            "Return only valid JSON with this exact shape:\n"
            '{"tasks":[{"id":"t1","type":"action","description":"..."},'
            '{"id":"v1","type":"validation","description":"...",'
            '"validates":"t1"},{"id":"t2","type":"action",'
            '"description":"..."},{"id":"v2","type":"validation",'
            '"description":"...","validates":"t2"}]}\n'
            f"User request: {prompt}"
        ),
        stream=False,
        think=DEFAULT_OLLAMA_THINK,
        format="json",
        options={"temperature": 0, "num_predict": 512},
        keep_alive=DEFAULT_OLLAMA_KEEP_ALIVE,
    )

    tasks = normalize_tasks(parse_tasks(response.get("response", "")))
    if tasks:
        action_count = sum(1 for task in tasks if task.get("type") == "action")
        if action_count == 1 and looks_multi_step():
            return fallback_tasks()
        return tasks

    return fallback_tasks()


def ai_response(
    prompt: str,
    messages: list[dict[str, Any]] | None = None,
    ) -> str:
    selected_tools = select_tools_for_prompt(prompt, limit=20, messages=messages)
    print(f"Tools: {[tool['function']['name'] for tool in selected_tools]}")

    return ollama_call(
        prompt,
        tools=selected_tools,
        messages=messages,
        think=DEFAULT_OLLAMA_THINK,
        return_messages=True,
    )





def ollama_call(
    prompt: str,
    *,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_category: str | None = None,
    model: str | None = None,
    system: str | None = None,
    options: dict[str, Any] | None = None,
    max_tool_rounds: int = 5,
    temperature: float | None = None,
    keep_alive: str | int | None = DEFAULT_OLLAMA_KEEP_ALIVE,
    think: bool | None = DEFAULT_OLLAMA_THINK,
    return_messages: bool = False,
    ) -> str | dict[str, Any]:
    """
    One-shot Ollama tool-calling helper.

    Args:
        prompt:
            User prompt.

        tools:
            Optional tool definitions. Defaults to ALL_TOOLS. Each tool may
            include function.function_object, which is used locally and removed
            before sending the schema to Ollama.

        messages:
            Optional shared message history. When provided, this function
            appends the user prompt, assistant responses, and tool results to
            the same list so later calls keep context.

        tool_category:
            Optional ALL_TOOLS category filter.

        model:
            Ollama model name. Defaults to OLLAMA_MODEL from .env.

        system:
            Optional system prompt.

        options:
            Optional Ollama options dict.

        max_tool_rounds:
            Maximum tool-call loops before stopping.

        temperature:
            Convenience shortcut added into options.

        keep_alive:
            Optional Ollama keep_alive value. Defaults to OLLAMA_KEEP_ALIVE
            from .env when set.

        think:
            Optional Ollama thinking mode flag. Defaults to OLLAMA_THINK from
            .env.

        return_messages:
            If True, returns full messages and final response metadata.

    Returns:
        Final assistant text, or a dict with messages/response if return_messages=True.
    """

    def get_ollama_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected_tools = ALL_TOOLS if tools is None else tools

        if tool_category is not None:
            selected_tools = [
                tool for tool in selected_tools
                if tool.get("category") == tool_category
            ]

        ollama_tools: list[dict[str, Any]] = []
        tool_funcs: dict[str, Any] = {}

        for tool in selected_tools:
            function_schema = dict(tool["function"])
            function_object = function_schema.pop("function_object", None)
            function_name = function_schema["name"]

            ollama_tools.append({
                "type": tool.get("type", "function"),
                "function": function_schema,
            })

            if callable(function_object):
                tool_funcs[function_name] = function_object

        return ollama_tools, tool_funcs

    merged_options = dict(options or {})
    if temperature is not None:
        merged_options["temperature"] = temperature
    selected_model = model or DEFAULT_OLLAMA_MODEL
    ollama_tools, tool_funcs = get_ollama_tools()

    active_messages = messages if messages is not None else []

    if system:
        has_system = any(
            message.get("role") == "system" and message.get("content") == system
            for message in active_messages
        )
        if not has_system:
            active_messages.append({
                "role": "system",
                "content": system,
            })

    active_messages.append({
        "role": "user",
        "content": prompt,
    })

    final_response = None

    for _ in range(max_tool_rounds + 1):
        chat_kwargs = {
            "model": selected_model,
            "messages": active_messages,
            "stream": False,
        }

        if ollama_tools:
            chat_kwargs["tools"] = ollama_tools

        if merged_options:
            chat_kwargs["options"] = merged_options

        if keep_alive is not None:
            chat_kwargs["keep_alive"] = keep_alive

        if think is not None:
            chat_kwargs["think"] = think

        response = OLLAMA_CLIENT.chat(**chat_kwargs)
        final_response = response

        assistant_msg = response["message"]
        active_messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []

        if not tool_calls:
            content = assistant_msg.get("content", "")
            if return_messages:
                return {
                    "content": content,
                    "messages": active_messages,
                    "response": response,
                }
            return content

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments") or {}

            if name not in tool_funcs:
                tool_result = {
                    "error": f"Tool '{name}' is not registered in tool_funcs."
                }
            else:
                try:
                    tool_result = tool_funcs[name](**args)
                except Exception as exc:
                    tool_result = {
                        "error": str(exc),
                        "tool": name,
                        "arguments": args,
                    }

            active_messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(tool_result, default=str),
            })

    content = (
        final_response["message"].get("content", "")
        if final_response
        else ""
    )

    if return_messages:
        return {
            "content": content,
            "messages": active_messages,
            "response": final_response,
            "warning": "Reached max_tool_rounds before tool-calling completed.",
        }

    return content


def _memory_context(prompt: str, limit: int = 5) -> str:
    try:
        memories = search_memory(prompt, max_results=limit)
    except Exception:
        memories = []

    if not memories:
        return ""

    lines = ["Relevant durable memories from SQLite:"]
    for memory in memories:
        tags = ", ".join(memory.get("tags") or [])
        suffix = f" [{tags}]" if tags else ""
        lines.append(
            f"- {memory['title']}{suffix}: {memory['content']}"
        )
    return "\n".join(lines)


def get_starter_context(prompt):
    memory_context = _memory_context(prompt)
    system_content = (
        get_config("personality", "You are a helpful assistant that provides accurate and concise answers to user questions.") + " "
        "You are working through a multi-step user request. "
        "Use the conversation history, previous task outputs, and "
        "tool results as context for each next step."
        "Please use tools as and when necessary to complete the user request."
    )
    if memory_context:
        system_content = f"{system_content}\n\n{memory_context}"

    return [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

