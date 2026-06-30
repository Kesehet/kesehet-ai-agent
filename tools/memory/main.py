from typing import Any
from uuid import uuid4

from core.db import connect, dumps, init_db, row_to_memory, utc_now_text


def remember(title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    if not title.strip():
        raise ValueError("title is required.")
    if not content.strip():
        raise ValueError("content is required.")

    memory_id = uuid4().hex
    now = utc_now_text()
    normalized_tags = [tag.strip() for tag in (tags or []) if tag.strip()]
    init_db()
    with connect() as db:
        db.execute(
            """
            INSERT INTO memories (id, title, content, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                title.strip(),
                content.strip(),
                dumps(normalized_tags),
                now,
                now,
            ),
        )
    return get_memory(memory_id)


def search_memory(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    term = f"%{query.strip()}%"
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM memories
            WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (term, term, term, max(1, min(max_results, 30))),
        ).fetchall()
    return [row_to_memory(row) for row in rows]


def list_memories(max_results: int = 20) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(max_results, 100)),),
        ).fetchall()
    return [row_to_memory(row) for row in rows]


def get_memory(memory_id: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    if row is None:
        raise ValueError("Memory does not exist.")
    return row_to_memory(row)


def update_memory(
    memory_id: str,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    current = get_memory(memory_id)
    next_title = title.strip() if title is not None else current["title"]
    next_content = content.strip() if content is not None else current["content"]
    next_tags = (
        [tag.strip() for tag in tags if tag.strip()]
        if tags is not None
        else current["tags"]
    )
    if not next_title:
        raise ValueError("title is required.")
    if not next_content:
        raise ValueError("content is required.")

    with connect() as db:
        db.execute(
            """
            UPDATE memories
            SET title = ?, content = ?, tags = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_title,
                next_content,
                dumps(next_tags),
                utc_now_text(),
                memory_id,
            ),
        )
    return get_memory(memory_id)


def delete_memory(memory_id: str) -> dict[str, str]:
    get_memory(memory_id)
    with connect() as db:
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return {"deleted": memory_id}
