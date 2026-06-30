from pathlib import Path
from shutil import copy2, move
from typing import Any


INTERNAL_ROOT = Path(__file__).resolve().parents[2] / "internal"


def _ensure_root() -> None:
    INTERNAL_ROOT.mkdir(parents=True, exist_ok=True)


def _resolve_internal_path(path: str) -> Path:
    _ensure_root()
    target = (INTERNAL_ROOT / path).resolve()
    root = INTERNAL_ROOT.resolve()

    if target != root and root not in target.parents:
        raise ValueError("Path is outside the internal folder.")

    return target


def list_files(path: str = ".", recursive: bool = False) -> list[dict[str, Any]]:
    base_path = _resolve_internal_path(path)
    if not base_path.exists():
        return []
    if not base_path.is_dir():
        raise ValueError("Path is not a directory.")

    entries = base_path.rglob("*") if recursive else base_path.iterdir()
    return [
        {
            "path": str(entry.relative_to(INTERNAL_ROOT)),
            "type": "directory" if entry.is_dir() else "file",
            "size": entry.stat().st_size if entry.is_file() else None,
        }
        for entry in entries
    ]


def read_file(path: str) -> str:
    file_path = _resolve_internal_path(path)
    if not file_path.is_file():
        raise ValueError("File does not exist.")

    return file_path.read_text(encoding="utf-8")


def write_file(path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    file_path = _resolve_internal_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.exists() and not overwrite:
        raise ValueError("File already exists.")

    file_path.write_text(content, encoding="utf-8")
    return {
        "path": str(file_path.relative_to(INTERNAL_ROOT)),
        "size": file_path.stat().st_size,
    }


def append_file(path: str, content: str) -> dict[str, Any]:
    file_path = _resolve_internal_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("a", encoding="utf-8") as file:
        file.write(content)

    return {
        "path": str(file_path.relative_to(INTERNAL_ROOT)),
        "size": file_path.stat().st_size,
    }


def delete_file(path: str) -> dict[str, str]:
    file_path = _resolve_internal_path(path)
    if not file_path.is_file():
        raise ValueError("File does not exist.")

    file_path.unlink()
    return {"deleted": str(file_path.relative_to(INTERNAL_ROOT))}


def create_directory(path: str) -> dict[str, str]:
    directory_path = _resolve_internal_path(path)
    directory_path.mkdir(parents=True, exist_ok=True)
    return {"path": str(directory_path.relative_to(INTERNAL_ROOT))}


def delete_directory(path: str) -> dict[str, str]:
    directory_path = _resolve_internal_path(path)
    if directory_path == INTERNAL_ROOT.resolve():
        raise ValueError("Cannot delete the internal root folder.")
    if not directory_path.is_dir():
        raise ValueError("Directory does not exist.")
    if any(directory_path.iterdir()):
        raise ValueError("Directory is not empty.")

    directory_path.rmdir()
    return {"deleted": str(directory_path.relative_to(INTERNAL_ROOT))}


def move_path(source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, str]:
    source = _resolve_internal_path(source_path)
    destination = _resolve_internal_path(destination_path)

    if not source.exists():
        raise ValueError("Source path does not exist.")
    if destination.exists() and not overwrite:
        raise ValueError("Destination path already exists.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    move(str(source), str(destination))
    return {
        "source": str(source.relative_to(INTERNAL_ROOT)),
        "destination": str(destination.relative_to(INTERNAL_ROOT)),
    }


def copy_file(source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, str]:
    source = _resolve_internal_path(source_path)
    destination = _resolve_internal_path(destination_path)

    if not source.is_file():
        raise ValueError("Source file does not exist.")
    if destination.exists() and not overwrite:
        raise ValueError("Destination path already exists.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    copy2(source, destination)
    return {
        "source": str(source.relative_to(INTERNAL_ROOT)),
        "destination": str(destination.relative_to(INTERNAL_ROOT)),
    }


def get_file_info(path: str) -> dict[str, Any]:
    target = _resolve_internal_path(path)
    if not target.exists():
        raise ValueError("Path does not exist.")

    stat = target.stat()
    return {
        "path": str(target.relative_to(INTERNAL_ROOT)),
        "type": "directory" if target.is_dir() else "file",
        "size": stat.st_size if target.is_file() else None,
        "modified_at": stat.st_mtime,
    }


def search_files(query: str, path: str = ".", max_results: int = 20) -> list[dict[str, Any]]:
    base_path = _resolve_internal_path(path)
    if not base_path.is_dir():
        raise ValueError("Path is not a directory.")

    results: list[dict[str, Any]] = []
    for file_path in base_path.rglob("*"):
        if not file_path.is_file():
            continue

        relative_path = str(file_path.relative_to(INTERNAL_ROOT))
        if query.lower() in relative_path.lower():
            results.append({
                "path": relative_path,
                "match": "filename",
            })
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            if query.lower() in content.lower():
                results.append({
                    "path": relative_path,
                    "match": "content",
                })

        if len(results) >= max_results:
            break

    return results
