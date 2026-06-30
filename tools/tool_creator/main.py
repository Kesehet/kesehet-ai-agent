import ast
import importlib
import json
import re
import textwrap
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
FUNCTION_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_package_name(name: str) -> str:
    normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not PACKAGE_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "package_name must start with a letter and contain only letters, numbers, and underscores."
        )
    if normalized.startswith("__"):
        raise ValueError("package_name cannot start with double underscores.")

    return normalized


def _validate_function_name(name: str) -> str:
    normalized = name.strip()
    if not FUNCTION_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "function_name must be a valid Python function name."
        )
    return normalized


def _validate_parameters_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}, "required": []}

    if not isinstance(schema, dict):
        raise ValueError("parameters_schema must be a JSON object.")
    if schema.get("type") != "object":
        raise ValueError('parameters_schema.type must be "object".')

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if not isinstance(properties, dict):
        raise ValueError("parameters_schema.properties must be an object.")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("parameters_schema.required must be a list of strings.")

    unknown_required = [item for item in required if item not in properties]
    if unknown_required:
        raise ValueError(
            f"Required parameters are missing from properties: {', '.join(unknown_required)}"
        )

    normalized = dict(schema)
    normalized["properties"] = properties
    normalized["required"] = required
    return normalized


def _default_implementation(function_name: str) -> str:
    return textwrap.dedent(
        f'''\
        from typing import Any


        def {function_name}(**kwargs: Any) -> dict[str, Any]:
            """
            Replace this scaffold with the tool implementation.
            """
            return {{
                "status": "scaffold",
                "message": "Tool created. Implement its behavior in main.py.",
                "received": kwargs,
            }}
        '''
    )


def _validate_implementation(source: str, function_name: str) -> None:
    try:
        tree = ast.parse(source)
        compile(source, "<generated_tool>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"implementation_code contains invalid Python: {exc}") from exc

    has_function = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        for node in tree.body
    )
    if not has_function:
        raise ValueError(
            f"implementation_code must define a top-level function named {function_name}."
        )


def _write_text(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ValueError(f"{path.name} already exists.")
    path.write_text(content, encoding="utf-8")


def diagnose_tool_packages(package_name: str | None = None) -> list[dict[str, Any]]:
    """
    Validate tool package structure, manifests, syntax, and entrypoints.
    """
    from tools.main import _load_manifest, validate_tool_package

    package_filter = _validate_package_name(package_name) if package_name else None
    results: list[dict[str, Any]] = []

    for tool_dir in sorted(path for path in TOOLS_ROOT.iterdir() if path.is_dir()):
        if tool_dir.name.startswith("__"):
            continue
        if package_filter and tool_dir.name != package_filter:
            continue

        issues: list[str] = []
        manifest: dict[str, Any] | None = None

        try:
            validate_tool_package(tool_dir)
        except Exception as exc:
            issues.append(str(exc))

        try:
            manifest = _load_manifest(tool_dir / "manifest.json")
        except Exception as exc:
            issues.append(str(exc))

        main_path = tool_dir / "main.py"
        if main_path.is_file():
            try:
                source = main_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                compile(source, str(main_path), "exec")
                defined_functions = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            except Exception as exc:
                defined_functions = set()
                issues.append(f"{main_path} has invalid Python: {exc}")
        else:
            defined_functions = set()

        if manifest is not None:
            module_name = manifest["module"]
            if module_name != f"tools.{tool_dir.name}.main":
                issues.append(
                    f"manifest module should normally be tools.{tool_dir.name}.main, found {module_name}."
                )

            for index, tool_config in enumerate(manifest["tools"]):
                if not isinstance(tool_config, dict):
                    issues.append(f"tools[{index}] must be an object.")
                    continue

                function = tool_config.get("function")
                if not isinstance(function, dict):
                    issues.append(f"tools[{index}] is missing function schema.")
                    continue

                function_name = function.get("name")
                entrypoint = tool_config.get("entrypoint", function_name)
                if not isinstance(function_name, str) or not function_name:
                    issues.append(f"tools[{index}].function.name must be a non-empty string.")
                if not isinstance(entrypoint, str) or not entrypoint:
                    issues.append(f"tools[{index}].entrypoint must be a non-empty string.")
                elif entrypoint not in defined_functions:
                    issues.append(f"entrypoint {entrypoint} is not defined in main.py.")

            try:
                module = importlib.import_module(module_name)
                for tool_config in manifest["tools"]:
                    if isinstance(tool_config, dict):
                        function = tool_config.get("function", {})
                        entrypoint = tool_config.get("entrypoint", function.get("name"))
                        if isinstance(entrypoint, str) and not callable(getattr(module, entrypoint, None)):
                            issues.append(f"entrypoint {entrypoint} is not callable after import.")
            except Exception as exc:
                issues.append(f"could not import {module_name}: {exc}")

        results.append({
            "package": tool_dir.name,
            "ok": not issues,
            "issues": issues,
        })

    if package_filter and not results:
        return [{
            "package": package_filter,
            "ok": False,
            "issues": ["Tool package does not exist."],
        }]

    return results


def create_tool_package(
    package_name: str,
    function_name: str,
    description: str,
    parameters_schema: dict[str, Any] | None = None,
    implementation_code: str | None = None,
    category: str = "custom",
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    Create a complete tool package under the tools folder.
    """
    from tools.main import refresh_tools, validate_tool_package

    safe_package_name = _validate_package_name(package_name)
    safe_function_name = _validate_function_name(function_name)
    safe_description = description.strip()
    safe_category = category.strip() or "custom"
    schema = _validate_parameters_schema(parameters_schema)

    if not safe_description:
        raise ValueError("description is required.")

    tool_dir = (TOOLS_ROOT / safe_package_name).resolve()
    tools_root = TOOLS_ROOT.resolve()
    if tool_dir != tools_root and tools_root not in tool_dir.parents:
        raise ValueError("Resolved tool path is outside the tools folder.")

    if tool_dir.exists() and not tool_dir.is_dir():
        raise ValueError("Target tool path exists and is not a directory.")
    if tool_dir.exists() and any(tool_dir.iterdir()) and not overwrite:
        raise ValueError("Tool package already exists. Set overwrite=true to replace standard files.")

    source = implementation_code if implementation_code is not None else _default_implementation(safe_function_name)
    source = source.strip() + "\n"
    _validate_implementation(source, safe_function_name)

    manifest = {
        "name": safe_package_name,
        "module": f"tools.{safe_package_name}.main",
        "tools": [
            {
                "type": "function",
                "category": safe_category,
                "function": {
                    "name": safe_function_name,
                    "description": safe_description,
                    "parameters": schema,
                },
                "entrypoint": safe_function_name,
            }
        ],
    }

    test_class_name = "".join(part.capitalize() for part in safe_package_name.split("_")) + "ManifestTest"
    test_source = textwrap.dedent(
        f'''\
        from pathlib import Path
        from unittest import TestCase

        from tools.main import validate_tool_package


        class {test_class_name}(TestCase):
            def test_tool_package_is_valid(self) -> None:
                validate_tool_package(Path(__file__).resolve().parent)
        '''
    )

    tool_dir.mkdir(parents=True, exist_ok=True)
    _write_text(tool_dir / "__init__.py", "", overwrite=True)
    _write_text(tool_dir / "main.py", source, overwrite=overwrite)
    _write_text(
        tool_dir / "manifest.json",
        json.dumps(manifest, indent=2) + "\n",
        overwrite=overwrite,
    )
    _write_text(tool_dir / "test_manifest.py", test_source, overwrite=overwrite)

    validate_tool_package(tool_dir)
    refresh_tools()

    return {
        "package": safe_package_name,
        "function": safe_function_name,
        "category": safe_category,
        "files": [
            str((tool_dir / "__init__.py").relative_to(tools_root)),
            str((tool_dir / "main.py").relative_to(tools_root)),
            str((tool_dir / "manifest.json").relative_to(tools_root)),
            str((tool_dir / "test_manifest.py").relative_to(tools_root)),
        ],
        "message": "Tool package created and registry refreshed.",
    }


def repair_tool_package(
    package_name: str,
    function_name: str,
    description: str,
    parameters_schema: dict[str, Any] | None = None,
    implementation_code: str | None = None,
    category: str = "custom",
) -> dict[str, Any]:
    """
    Replace a tool package's standard files with corrected code and manifest data.
    """
    result = create_tool_package(
        package_name=package_name,
        function_name=function_name,
        description=description,
        parameters_schema=parameters_schema,
        implementation_code=implementation_code,
        category=category,
        overwrite=True,
    )
    result["message"] = "Tool package repaired and registry refreshed."
    return result
