from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4


SUPPORTED_MODELS = [
    "glm-4.5-air",
    "glm-4.5",
    "glm-4.6",
    "glm-4.7",
    "glm-4.7-flash",
    "glm-5",
    "glm-5.1",
]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            role=str(data.get("role", "user")),
            content=str(data.get("content", "")),
            created_at=str(data.get("created_at", now_iso())),
        )


@dataclass
class ChatSession:
    name: str = "默认会话"
    model: str = "glm-4.5-air"
    temperature: float = 1.0
    stream: bool = True
    thinking: Optional[bool] = None
    mode: str = "auto"
    rag_enabled: bool = True
    approval_policy: str = "strict"
    last_change_id: Optional[str] = None
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    messages: List[ChatMessage] = field(default_factory=list)
    context_files: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = now_iso()

    def add_message(self, role: str, content: str) -> ChatMessage:
        message = ChatMessage(role=role, content=content)
        self.messages.append(message)
        self.touch()
        return message

    def clear_messages(self) -> None:
        self.messages.clear()
        self.usage.clear()
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "temperature": self.temperature,
            "stream": self.stream,
            "thinking": self.thinking,
            "mode": self.mode,
            "rag_enabled": self.rag_enabled,
            "approval_policy": self.approval_policy,
            "last_change_id": self.last_change_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [message.to_dict() for message in self.messages],
            "context_files": list(self.context_files),
            "usage": dict(self.usage),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatSession":
        session = cls(
            name=str(data.get("name", "默认会话")),
            model=str(data.get("model", "glm-4.5-air")),
            temperature=float(data.get("temperature", 1.0)),
            stream=bool(data.get("stream", True)),
            thinking=data.get("thinking"),
            mode=str(data.get("mode", "auto")),
            rag_enabled=bool(data.get("rag_enabled", True)),
            approval_policy=str(data.get("approval_policy", "strict")),
            last_change_id=data.get("last_change_id"),
            id=str(data.get("id", uuid4().hex[:12])),
            created_at=str(data.get("created_at", now_iso())),
            updated_at=str(data.get("updated_at", now_iso())),
        )
        session.messages = [
            ChatMessage.from_dict(item)
            for item in data.get("messages", [])
            if isinstance(item, dict)
        ]
        session.context_files = [
            str(item) for item in data.get("context_files", []) if str(item).strip()
        ]
        usage = data.get("usage", {})
        session.usage = usage if isinstance(usage, dict) else {}
        return session

    def api_messages(
        self,
        system_prompt: str,
        max_history: int = 24,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]
        history = self.messages[-max_history:]
        messages.extend(
            {"role": item.role, "content": item.content}
            for item in history
            if item.role in {"user", "assistant", "system"}
        )
        return messages
