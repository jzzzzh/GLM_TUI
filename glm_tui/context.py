from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


MENTION_RE = re.compile(r"@([A-Za-z0-9_./\\-]+)")
SKIP_DIRS = {".git", ".venv", ".glm_tui", "__pycache__", "node_modules", ".idea"}
TEXT_SUFFIXES = {
    ".py",
    ".sh",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".css",
    ".html",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sql",
    ".csv",
}


@dataclass
class ContextFile:
    path: str
    content: str
    truncated: bool = False

    def as_prompt_block(self) -> str:
        suffix = "（已截断）" if self.truncated else ""
        return f"### 文件：{self.path}{suffix}\n```\n{self.content}\n```"


@dataclass
class GrepHit:
    path: str
    line_no: int
    line: str


class ProjectContext:
    def __init__(self, root: Path, max_file_bytes: int = 20_000):
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes

    def resolve(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("只允许读取当前项目目录内的文件") from exc
        return path

    def read_file(self, raw_path: str) -> ContextFile:
        path = self.resolve(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在：{raw_path}")
        if not path.is_file():
            raise ValueError(f"不是普通文件：{raw_path}")
        if self._looks_binary(path):
            raise ValueError(f"疑似二进制文件，已跳过：{raw_path}")
        raw = path.read_bytes()
        truncated = len(raw) > self.max_file_bytes
        raw = raw[: self.max_file_bytes]
        content = raw.decode("utf-8", errors="replace")
        rel = path.relative_to(self.root).as_posix()
        return ContextFile(path=rel, content=content, truncated=truncated)

    def find_mentions(self, text: str) -> List[str]:
        seen = set()
        result: List[str] = []
        for match in MENTION_RE.finditer(text):
            item = match.group(1).strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    def grep(self, keyword: str, limit: int = 80) -> List[GrepHit]:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("搜索关键词不能为空")
        hits: List[GrepHit] = []
        for path in self._iter_text_files():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for line_no, line in enumerate(handle, 1):
                        if keyword.lower() in line.lower():
                            rel = path.relative_to(self.root).as_posix()
                            hits.append(GrepHit(rel, line_no, line.strip()))
                            if len(hits) >= limit:
                                return hits
            except OSError:
                continue
        return hits

    def _iter_text_files(self) -> Iterable[Path]:
        for current_root, dirs, files in os.walk(self.root):
            dirs[:] = [item for item in dirs if item not in SKIP_DIRS and not item.startswith(".")]
            for name in files:
                path = Path(current_root) / name
                if path.suffix.lower() in TEXT_SUFFIXES and not self._looks_binary(path):
                    yield path

    def _looks_binary(self, path: Path) -> bool:
        if path.suffix.lower() in TEXT_SUFFIXES:
            return False
        try:
            sample = path.read_bytes()[:1024]
        except OSError:
            return True
        return b"\0" in sample
