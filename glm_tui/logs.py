from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from .storage import read_json, redact_secrets, write_json


@dataclass
class TurnRecord:
    turn_id: str
    session_id: str
    created_at: str
    user_input: str
    assistant_output: str
    model: str
    context_files: List[str] = field(default_factory=list)
    change_id: Optional[str] = None
    intent: str = "chat"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "user_input": self.user_input,
            "assistant_output": self.assistant_output,
            "model": self.model,
            "context_files": list(self.context_files),
            "change_id": self.change_id,
            "intent": self.intent,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TurnRecord":
        return cls(
            turn_id=str(data.get("turn_id", "")),
            session_id=str(data.get("session_id", "")),
            created_at=str(data.get("created_at", "")),
            user_input=str(data.get("user_input", "")),
            assistant_output=str(data.get("assistant_output", "")),
            model=str(data.get("model", "")),
            context_files=[str(item) for item in data.get("context_files", [])],
            change_id=data.get("change_id"),
            intent=str(data.get("intent", "chat")),
        )


class LogStore:
    def __init__(self, root: Path):
        self.root = root / ".glm_tui" / "logs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.turns_path = self.root / "turns.jsonl"
        self.state_path = self.root / "state.json"

    def append(
        self,
        *,
        session_id: str,
        user_input: str,
        assistant_output: str,
        model: str,
        context_files: Iterable[str],
        change_id: Optional[str],
        intent: str,
    ) -> TurnRecord:
        record = TurnRecord(
            turn_id=uuid4().hex[:12],
            session_id=session_id,
            created_at=datetime.now().replace(microsecond=0).isoformat(),
            user_input=redact_secrets(user_input),
            assistant_output=redact_secrets(assistant_output),
            model=model,
            context_files=list(context_files),
            change_id=change_id,
            intent=intent,
        )
        with self.turns_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        return record

    def read_all(self, limit: Optional[int] = None) -> List[TurnRecord]:
        if not self.turns_path.exists():
            return []
        records: List[TurnRecord] = []
        with self.turns_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    records.append(TurnRecord.from_dict(data))
        return records[-limit:] if limit else records

    def state(self) -> Dict[str, Any]:
        return read_json(self.state_path, {"last_summarized_turn_id": None})

    def mark_summarized(self, records: List[TurnRecord]) -> None:
        if not records:
            return
        state = self.state()
        state["last_summarized_turn_id"] = records[-1].turn_id
        state["summarized_at"] = datetime.now().replace(microsecond=0).isoformat()
        write_json(self.state_path, state)

    def read_unsummarized(self, limit: int = 50) -> List[TurnRecord]:
        records = self.read_all()
        last_id = self.state().get("last_summarized_turn_id")
        if not last_id:
            return records[-limit:]
        for index, record in enumerate(records):
            if record.turn_id == last_id:
                return records[index + 1 : index + 1 + limit]
        return records[-limit:]

    def get_turn(self, turn_id: str) -> Optional[TurnRecord]:
        for record in self.read_all():
            if record.turn_id == turn_id:
                return record
        return None

    def search(self, keyword: str, limit: int = 20) -> List[TurnRecord]:
        keyword = keyword.lower().strip()
        if not keyword:
            return self.read_all(limit=limit)
        result = [
            record
            for record in self.read_all()
            if keyword in (record.user_input + "\n" + record.assistant_output).lower()
        ]
        return result[-limit:]
