from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .state import ChatSession


APP_DIR_NAME = ".glm_tui"


SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(ZHIPUAI_API_KEY\s*=\s*)[A-Za-z0-9._\-]+"),
    re.compile(r"([A-Za-z0-9]{16,}\.[A-Za-z0-9._\-]{12,})"),
]


def app_dir(root: Path) -> Path:
    return root / APP_DIR_NAME


def redact_secrets(text: str) -> str:
    redacted = text
    env_token = os.getenv("ZHIPUAI_API_KEY")
    if env_token:
        redacted = redacted.replace(env_token, "***")
    redacted = SECRET_PATTERNS[0].sub("Bearer ***", redacted)
    redacted = SECRET_PATTERNS[1].sub(r"\1***", redacted)
    redacted = SECRET_PATTERNS[2].sub("***", redacted)
    return redacted


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class MemoryStore:
    def __init__(self, root: Path):
        self.root = app_dir(root)
        self.path = self.root / "memory.json"
        self.skills_dir = self.root / "skills"
        self.data: Dict[str, Any] = read_json(
            self.path,
            {
                "preferences": {},
                "notes": {},
                "recaps": [],
                "last_model": "glm-4.5-air",
            },
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_default_skills()

    def save(self) -> None:
        write_json(self.path, self.data)

    def remember(self, key: str, value: str) -> None:
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError("记忆键不能为空")
        self.data.setdefault("preferences", {})[key] = value
        self.save()

    def forget(self, key: str) -> bool:
        key = key.strip()
        removed = False
        for section in ("preferences", "notes"):
            values = self.data.get(section, {})
            if isinstance(values, dict) and key in values:
                del values[key]
                removed = True
        if removed:
            self.save()
        return removed

    def add_recap(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        recaps = self.data.setdefault("recaps", [])
        recaps.append(
            {
                "created_at": datetime.now().replace(microsecond=0).isoformat(),
                "content": text,
            }
        )
        self.data["recaps"] = recaps[-20:]
        self.save()

    def set_last_model(self, model: str) -> None:
        self.data["last_model"] = model
        self.save()

    def summary(self, max_items: int = 12) -> str:
        lines: List[str] = []
        preferences = self.data.get("preferences", {})
        if isinstance(preferences, dict) and preferences:
            lines.append("用户偏好：")
            for key, value in list(preferences.items())[:max_items]:
                lines.append(f"- {key}: {value}")
        notes = self.data.get("notes", {})
        if isinstance(notes, dict) and notes:
            lines.append("项目笔记：")
            for key, value in list(notes.items())[:max_items]:
                lines.append(f"- {key}: {value}")
        recaps = self.data.get("recaps", [])
        if isinstance(recaps, list) and recaps:
            lines.append("最近摘要：")
            for item in recaps[-3:]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('content', '')}")
        return "\n".join(lines).strip()

    def display(self) -> str:
        body = self.summary(max_items=50)
        return body if body else "暂无长期记忆。可使用 `/remember 风格=回答尽量简洁` 添加。"

    def ensure_default_skills(self) -> None:
        defaults = {
            "代码解释.md": "请用中文解释下面的代码，重点说明执行流程、关键变量和潜在风险：\n\n{input}\n",
            "错误排查.md": "请作为资深工程师，用中文分析这个错误。请给出原因、定位步骤和最小修复建议：\n\n{input}\n",
            "重构建议.md": "请审查下面的代码或设计，并给出务实的重构建议。要求按收益和风险排序：\n\n{input}\n",
            "测试生成.md": "请为下面的功能或代码设计测试用例。覆盖正常路径、边界情况和失败模式：\n\n{input}\n",
            "架构评审.md": "请从架构角度评审下面的方案或代码。请关注模块边界、数据流、扩展性、失败模式和最小改进路径：\n\n{input}\n",
            "PR审查.md": "请以代码审查者身份审查下面的改动。先列风险和 bug，再列可维护性建议，最后给出是否建议合并：\n\n{input}\n",
            "需求澄清.md": "请把下面的需求整理成可执行规格。请明确目标、非目标、输入输出、边界情况、验收标准和待确认问题：\n\n{input}\n",
            "提交信息.md": "请根据下面的改动内容生成中文 Git commit message。要求标题简短，正文列出关键变更和验证方式：\n\n{input}\n",
            "文档生成.md": "请为下面的功能或代码生成中文文档。要求包括用途、安装/运行方式、主要命令、示例和注意事项：\n\n{input}\n",
            "性能分析.md": "请分析下面代码或日志中的性能瓶颈。请按影响程度排序，并给出可验证的优化方案：\n\n{input}\n",
            "安全审计.md": "请对下面代码或方案做安全审计。关注密钥泄露、路径穿越、注入、权限、日志暴露和数据外传风险：\n\n{input}\n",
            "API调试.md": "请帮助调试下面的 API 请求或响应。请检查 URL、header、payload、状态码、错误体和最小复现方式：\n\n{input}\n",
            "TUI设计.md": "请作为终端产品设计师，改进下面的 TUI 体验。关注信息架构、快捷键、视觉层级、空状态和错误反馈：\n\n{input}\n",
            "学习导师.md": "请把下面的问题当作教学材料，用中文分层讲解。先给直觉解释，再给关键概念，最后给练习建议：\n\n{input}\n",
        }
        for name, content in defaults.items():
            path = self.skills_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def list_skills(self) -> List[str]:
        return sorted(path.stem for path in self.skills_dir.glob("*.md"))

    def load_skill(self, name: str) -> Optional[str]:
        normalized = name.strip().removesuffix(".md")
        for path in self.skills_dir.glob("*.md"):
            if path.stem == normalized:
                return path.read_text(encoding="utf-8")
        return None


class SessionStore:
    def __init__(self, root: Path):
        self.root = app_dir(root)
        self.sessions_dir = self.root / "sessions"
        self.exports_dir = self.root / "exports"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: ChatSession) -> Path:
        path = self.sessions_dir / f"{session.id}.json"
        write_json(path, session.to_dict())
        return path

    def load_latest(self) -> Optional[ChatSession]:
        paths = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            data = read_json(path, None)
            if isinstance(data, dict):
                return ChatSession.from_dict(data)
        return None

    def list_sessions(self) -> List[Path]:
        return sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )

    def export_markdown(self, session: ChatSession, target: Optional[str] = None) -> Path:
        if target:
            path = Path(target).expanduser()
            if not path.is_absolute():
                path = self.exports_dir / path
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.exports_dir / f"{session.name}-{stamp}.md"
        if path.suffix.lower() != ".md":
            path = path.with_suffix(".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(session), encoding="utf-8")
        return path

    def to_markdown(self, session: ChatSession) -> str:
        lines = [
            f"# {session.name}",
            "",
            f"- 会话 ID: `{session.id}`",
            f"- 模型: `{session.model}`",
            f"- 温度: `{session.temperature}`",
            f"- 创建时间: `{session.created_at}`",
            f"- 更新时间: `{session.updated_at}`",
            "",
        ]
        if session.context_files:
            lines.append("## 上下文文件")
            lines.extend(f"- `{item}`" for item in session.context_files)
            lines.append("")
        lines.append("## 对话")
        for message in session.messages:
            title = "用户" if message.role == "user" else "助手"
            if message.role == "system":
                title = "系统"
            lines.extend(
                [
                    "",
                    f"### {title} · {message.created_at}",
                    "",
                    redact_secrets(message.content),
                ]
            )
        return redact_secrets("\n".join(lines).strip() + "\n")


def format_paths(paths: Iterable[Path]) -> str:
    names = [path.name for path in paths]
    return "\n".join(f"- `{name}`" for name in names) if names else "暂无会话。"
