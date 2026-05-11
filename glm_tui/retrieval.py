from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .logs import TurnRecord


@dataclass
class SearchHit:
    kind: str
    item_id: str
    title: str
    content: str
    score: float
    metadata: Dict[str, Any]


class RetrievalIndex:
    def __init__(self, root: Path):
        self.root = root / ".glm_tui" / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "index.sqlite"
        self.has_fts = True
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    created_at TEXT,
                    user_input TEXT,
                    assistant_output TEXT,
                    model TEXT,
                    context_files TEXT,
                    change_id TEXT,
                    intent TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_segments (
                    memory_id TEXT PRIMARY KEY,
                    content TEXT,
                    tags TEXT,
                    source_turn_ids TEXT,
                    source_session_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(turn_id UNINDEXED, content)"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(memory_id UNINDEXED, content)"
                )
            except sqlite3.OperationalError:
                self.has_fts = False

    def upsert_turn(self, record: TurnRecord) -> None:
        content = record.user_input + "\n" + record.assistant_output
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO turns
                (turn_id, session_id, created_at, user_input, assistant_output, model, context_files, change_id, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.turn_id,
                    record.session_id,
                    record.created_at,
                    record.user_input,
                    record.assistant_output,
                    record.model,
                    json.dumps(record.context_files, ensure_ascii=False),
                    record.change_id,
                    record.intent,
                ),
            )
            if self.has_fts:
                conn.execute("DELETE FROM turns_fts WHERE turn_id = ?", (record.turn_id,))
                conn.execute(
                    "INSERT INTO turns_fts(turn_id, content) VALUES (?, ?)",
                    (record.turn_id, content),
                )

    def upsert_segment(self, segment: Dict[str, Any]) -> None:
        memory_id = str(segment.get("memory_id", "")).strip()
        content = str(segment.get("content") or segment.get("summary") or "").strip()
        if not memory_id or not content:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_segments
                (memory_id, content, tags, source_turn_ids, source_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    content,
                    json.dumps(segment.get("tags", []), ensure_ascii=False),
                    json.dumps(segment.get("source_turn_ids", []), ensure_ascii=False),
                    segment.get("source_session_id"),
                    segment.get("created_at"),
                    segment.get("updated_at"),
                ),
            )
            if self.has_fts:
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
                conn.execute(
                    "INSERT INTO memory_fts(memory_id, content) VALUES (?, ?)",
                    (memory_id, content),
                )

    def delete_segment(self, memory_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM memory_segments WHERE memory_id = ?", (memory_id,))
            if self.has_fts:
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))

    def rebuild_turns(self, records: Iterable[TurnRecord]) -> None:
        for record in records:
            self.upsert_turn(record)

    def search_segments(self, query: str, limit: int = 6) -> List[SearchHit]:
        query = query.strip()
        if not query:
            return []
        like = f"%{query.lower()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_segments
                WHERE lower(content) LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (like, limit),
            ).fetchall()
        return [
            SearchHit(
                kind="mem",
                item_id=row["memory_id"],
                title=f"mem:{row['memory_id']}",
                content=row["content"],
                score=1.0,
                metadata=dict(row),
            )
            for row in rows
        ]

    def search_turns(self, query: str, limit: int = 8) -> List[SearchHit]:
        query = query.strip()
        if not query:
            return []
        like = f"%{query.lower()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM turns
                WHERE lower(user_input || ' ' || assistant_output) LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (like, limit),
            ).fetchall()
        return [
            SearchHit(
                kind="log",
                item_id=row["turn_id"],
                title=f"log:{row['session_id']}:{row['turn_id']}",
                content=f"用户：{row['user_input']}\n助手：{row['assistant_output']}",
                score=1.0,
                metadata=dict(row),
            )
            for row in rows
        ]

    def get_segment(self, memory_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_segments WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return dict(row) if row else None
