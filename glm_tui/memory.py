from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .api import GLMAPIError, GLMClient
from .logs import LogStore, TurnRecord
from .retrieval import RetrievalIndex, SearchHit
from .storage import read_json, redact_secrets, write_json


class MemoryManager:
    def __init__(self, root: Path):
        self.root = root / ".glm_tui" / "memory"
        self.root.mkdir(parents=True, exist_ok=True)
        self.summary_path = self.root / "summary.json"
        self.segments_path = self.root / "segments.jsonl"

    def load_summary(self) -> Dict[str, Any]:
        return read_json(self.summary_path, {"summary": "", "updated_at": None})

    def save_summary(self, summary: str) -> None:
        write_json(
            self.summary_path,
            {
                "summary": summary.strip(),
                "updated_at": datetime.now().replace(microsecond=0).isoformat(),
            },
        )

    def load_segments(self) -> List[Dict[str, Any]]:
        if not self.segments_path.exists():
            return []
        result: List[Dict[str, Any]] = []
        with self.segments_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    result.append(data)
        return result

    def save_segments(self, segments: List[Dict[str, Any]]) -> None:
        with self.segments_path.open("w", encoding="utf-8") as handle:
            for segment in segments:
                handle.write(json.dumps(segment, ensure_ascii=False) + "\n")

    def summary_text(self) -> str:
        return str(self.load_summary().get("summary", "")).strip()

    def context_for_query(self, query: str, retrieval: RetrievalIndex, detailed: bool) -> str:
        summary = self.summary_text()
        blocks: List[str] = []
        if summary:
            blocks.append("## 长期记忆总结\n" + summary)
        segment_hits = retrieval.search_segments(query, limit=4)
        if segment_hits:
            blocks.append("## 相关记忆片段\n" + "\n".join(format_hit(hit) for hit in segment_hits))
        if detailed:
            turn_hits = retrieval.search_turns(query, limit=5)
            if turn_hits:
                blocks.append("## 原始对话日志片段\n" + "\n\n".join(format_hit(hit) for hit in turn_hits))
        return "\n\n".join(blocks)

    async def sync_with_llm(
        self,
        *,
        client: GLMClient,
        model: str,
        logs: LogStore,
        retrieval: RetrievalIndex,
        limit: int = 50,
    ) -> List[str]:
        records = logs.read_unsummarized(limit=limit)
        if not records:
            return []
        old_summary = self.summary_text()
        turns = "\n\n".join(format_turn_for_memory(record) for record in records)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个本地记忆整理器。请只返回 JSON object，不要 Markdown。"
                    "根据旧记忆总结和新对话日志，生成滚动长期记忆和可检索记忆片段。"
                    "不要保存密钥、token、完整隐私内容。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请返回字段：updated_summary:string, added_segments:array, "
                    "updated_segments:array, obsolete_segments:array, display_changes:array。\n"
                    "每个 segment 至少包含 memory_id, content, tags, source_turn_ids, source_session_id。\n\n"
                    f"旧记忆总结：\n{old_summary or '暂无'}\n\n"
                    f"新对话日志：\n{turns}"
                ),
            },
        ]
        try:
            data = await client.complete_json(model=model, messages=messages, temperature=0.2, thinking=None)
        except GLMAPIError:
            data = self._fallback_memory_update(old_summary, records)
        return self.apply_update(data, records, logs, retrieval)

    def _fallback_memory_update(self, old_summary: str, records: List[TurnRecord]) -> Dict[str, Any]:
        source_turn_ids = [record.turn_id for record in records]
        latest = records[-1]
        content = f"最近对话包含：{latest.user_input[:160]}"
        summary = (old_summary + "\n" + content).strip() if old_summary else content
        return {
            "updated_summary": summary[-4000:],
            "added_segments": [
                {
                    "memory_id": "mem_" + uuid4().hex[:8],
                    "content": content,
                    "tags": ["自动摘要"],
                    "source_turn_ids": source_turn_ids,
                    "source_session_id": latest.session_id,
                }
            ],
            "updated_segments": [],
            "obsolete_segments": [],
            "display_changes": ["已用本地 fallback 生成记忆片段。"],
        }

    def apply_update(
        self,
        data: Dict[str, Any],
        records: List[TurnRecord],
        logs: LogStore,
        retrieval: RetrievalIndex,
    ) -> List[str]:
        now = datetime.now().replace(microsecond=0).isoformat()
        summary = str(data.get("updated_summary") or data.get("summary") or self.summary_text()).strip()
        if summary:
            self.save_summary(summary)

        existing = {str(item.get("memory_id")): item for item in self.load_segments() if item.get("memory_id")}
        for memory_id in data.get("obsolete_segments", []) or []:
            if isinstance(memory_id, dict):
                memory_id = str(memory_id.get("memory_id", ""))
            else:
                memory_id = str(memory_id)
            if not memory_id:
                continue
            existing.pop(memory_id, None)
            retrieval.delete_segment(memory_id)

        for field in ("added_segments", "updated_segments"):
            for segment in data.get(field, []) or []:
                if not isinstance(segment, dict):
                    continue
                memory_id = str(segment.get("memory_id") or ("mem_" + uuid4().hex[:8]))
                segment["memory_id"] = memory_id
                segment["content"] = redact_secrets(str(segment.get("content") or segment.get("summary") or ""))
                segment.setdefault("tags", [])
                segment.setdefault("source_turn_ids", [record.turn_id for record in records])
                segment.setdefault("source_session_id", records[-1].session_id if records else None)
                segment.setdefault("created_at", now)
                segment["updated_at"] = now
                if segment["content"]:
                    existing[memory_id] = segment
                    retrieval.upsert_segment(segment)

        self.save_segments(list(existing.values()))
        logs.mark_summarized(records)
        changes = data.get("display_changes") or []
        return [str(item) for item in changes if str(item).strip()]


def format_turn_for_memory(record: TurnRecord) -> str:
    return (
        f"[log:{record.session_id}:{record.turn_id}]\n"
        f"时间：{record.created_at}\n"
        f"意图：{record.intent}\n"
        f"用户：{record.user_input}\n"
        f"助手：{record.assistant_output[:1200]}"
    )


def format_hit(hit: SearchHit) -> str:
    label = f"[{hit.kind}:{hit.item_id}]"
    return f"{label} {hit.content[:1200]}"
