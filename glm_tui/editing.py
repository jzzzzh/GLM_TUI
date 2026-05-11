from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .checkpoints import CheckpointStore
from .context import TEXT_SUFFIXES
from .storage import redact_secrets


DENY_DIRS = {".git", ".venv", ".glm_tui", "__pycache__", "node_modules", ".idea"}
MAX_EDIT_FILE_BYTES = 600_000
EXPORT_TOKEN_RE = re.compile(r"^export\s+ZHIPUAI_API_KEY=.*$", re.MULTILINE)
DANGEROUS_DELETE_PATTERNS = [
    re.compile(r"\brm\s+(-[A-Za-z]*\s*)?[\w./~*$-]+"),
    re.compile(r"\brm\s+-[^\n;]*r[f]?\b"),
    re.compile(r"\brm\s+-[^\n;]*f[^\n;]*\s"),
    re.compile(r"\bshutil\.rmtree\s*\("),
    re.compile(r"\bos\.(remove|unlink|rmdir)\s*\("),
    re.compile(r"\.unlink\s*\("),
    re.compile(r"\.rmdir\s*\("),
    re.compile(r"\bsubprocess\.[^(]+\([^)]*rm\s"),
    re.compile(r"\bgit\s+clean\s+-"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
]


class EditError(RuntimeError):
    pass


@dataclass
class AppliedChange:
    change_id: str
    summary: str
    files: List[str]
    diff: str
    added_lines: int
    removed_lines: int


@dataclass
class EditPreview:
    summary: str
    plan: Dict[str, Any]
    files: List[str]
    diff: str
    added_lines: int
    removed_lines: int
    risk_level: str
    risk_reasons: List[str]
    operations: List[str]
    requires_single_approval: bool = False


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise EditError("模型没有返回 JSON 编辑计划")
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise EditError("编辑计划必须是 JSON object")
    return data


def resolve_edit_path(project_root: Path, raw_path: str) -> Path:
    if not raw_path or not str(raw_path).strip():
        raise EditError("编辑路径不能为空")
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    try:
        rel = path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise EditError(f"拒绝项目目录外路径：{raw_path}") from exc
    if any(part in DENY_DIRS for part in rel.parts):
        raise EditError(f"拒绝修改受保护路径：{rel.as_posix()}")
    return path


def ensure_text_file(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_file():
        raise EditError(f"不是普通文件：{path}")
    if path.stat().st_size > MAX_EDIT_FILE_BYTES:
        raise EditError(f"文件过大，拒绝自动编辑：{path}")
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore"}:
        sample = path.read_bytes()[:1024]
        if b"\0" in sample:
            raise EditError(f"疑似二进制文件，拒绝编辑：{path}")


def preserve_token_exports(original: str, new_content: str, path: Path) -> str:
    original_match = EXPORT_TOKEN_RE.search(original)
    if not original_match:
        return new_content
    original_line = original_match.group(0)
    if EXPORT_TOKEN_RE.search(new_content):
        return EXPORT_TOKEN_RE.sub(original_line, new_content)
    if path.name == "run.sh":
        lines = new_content.splitlines()
        insert_at = 1 if lines and lines[0].startswith("#!") else 0
        for index, line in enumerate(lines):
            if line.strip() == "set -euo pipefail":
                insert_at = index + 1
                break
        lines.insert(insert_at, original_line)
        return "\n".join(lines) + ("\n" if new_content.endswith("\n") else "")
    return new_content


def read_text(path: Path) -> str:
    ensure_text_file(path)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def normalize_plan(plan: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    summary = str(plan.get("summary") or plan.get("message") or "AI 自动修改")
    edits = plan.get("edits")
    if not isinstance(edits, list) or not edits:
        raise EditError("编辑计划缺少 edits 数组")
    normalized: List[Dict[str, Any]] = []
    for item in edits:
        if not isinstance(item, dict):
            raise EditError("每个 edit 必须是 object")
        edit_type = str(item.get("type", "")).strip()
        path = str(item.get("path", "")).strip()
        if edit_type not in {"write_file", "create_file", "replace_block", "delete_file", "copy_file"}:
            raise EditError(f"不支持的编辑类型：{edit_type}")
        if not path:
            raise EditError("edit 缺少 path")
        if edit_type == "copy_file" and not str(item.get("source") or item.get("source_path") or "").strip():
            raise EditError("copy_file 缺少 source")
        normalized.append({**item, "type": edit_type, "path": path})
    return summary, normalized


def summarize_diff(diff: str) -> Tuple[int, int]:
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def make_diff(project_root: Path, path: Path, before: str, after: str) -> str:
    rel = path.relative_to(project_root).as_posix()
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def candidate_context(project_root: Path, paths: Iterable[str], max_chars: int = 24_000) -> str:
    blocks: List[str] = []
    remaining = max_chars
    for raw_path in paths:
        try:
            path = resolve_edit_path(project_root, raw_path)
            content = redact_secrets(read_text(path))
        except (OSError, EditError):
            continue
        if not content:
            continue
        rel = path.relative_to(project_root).as_posix()
        block = f"### {rel}\n```\n{content[:remaining]}\n```"
        blocks.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


def source_path_for_copy(project_root: Path, item: Dict[str, Any]) -> Path:
    return resolve_edit_path(project_root, str(item.get("source") or item.get("source_path") or ""))


def detect_dangerous_delete_code(text: str) -> List[str]:
    hits: List[str] = []
    for pattern in DANGEROUS_DELETE_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def apply_edit_plan(
    project_root: Path,
    plan: Dict[str, Any],
    checkpoints: CheckpointStore,
) -> AppliedChange:
    project_root = project_root.resolve()
    summary, edits = normalize_plan(plan)
    paths = [resolve_edit_path(project_root, str(item["path"])) for item in edits]
    for path in paths:
        ensure_text_file(path)
    before_by_path = {path: read_text(path) for path in paths}
    checkpoint = checkpoints.create(paths, project_root, summary)
    try:
        for item in edits:
            path = resolve_edit_path(project_root, str(item["path"]))
            original = read_text(path)
            edit_type = str(item["type"])
            if edit_type == "delete_file":
                if path.exists():
                    path.unlink()
                continue
            if edit_type == "copy_file":
                source = source_path_for_copy(project_root, item)
                ensure_text_file(source)
                content = read_text(source)
                content = preserve_token_exports(original, content, path)
                write_text(path, content)
                continue
            if edit_type == "create_file" and path.exists():
                raise EditError(f"create_file 目标已存在：{item['path']}")
            if edit_type in {"write_file", "create_file"}:
                content = str(item.get("content", ""))
                content = preserve_token_exports(original, content, path)
                write_text(path, content)
                continue
            search = str(item.get("search", ""))
            replace = str(item.get("replace", ""))
            if not search:
                raise EditError(f"replace_block 缺少 search：{item['path']}")
            count = original.count(search)
            if count == 0:
                raise EditError(f"replace_block 未匹配：{item['path']}")
            if count > 1:
                raise EditError(f"replace_block 匹配多处，拒绝自动修改：{item['path']}")
            updated = original.replace(search, replace, 1)
            updated = preserve_token_exports(original, updated, path)
            write_text(path, updated)
    except Exception:
        checkpoints.restore(checkpoint.change_id, project_root)
        raise

    diff_parts: List[str] = []
    changed_files: List[str] = []
    for path in paths:
        before = before_by_path.get(path, "")
        after = read_text(path) if path.exists() else ""
        if before != after:
            changed_files.append(path.relative_to(project_root).as_posix())
            diff_parts.append(make_diff(project_root, path, before, after))
    diff = "\n".join(diff_parts)
    added, removed = summarize_diff(diff)
    return AppliedChange(
        change_id=checkpoint.change_id,
        summary=summary,
        files=changed_files,
        diff=diff,
        added_lines=added,
        removed_lines=removed,
    )


def preview_edit_plan(
    project_root: Path,
    plan: Dict[str, Any],
    allowed_paths: Iterable[str],
) -> EditPreview:
    project_root = project_root.resolve()
    summary, edits = normalize_plan(plan)
    allowed = {Path(item).as_posix() for item in allowed_paths}
    paths = [resolve_edit_path(project_root, str(item["path"])) for item in edits]
    before_by_path: Dict[Path, str] = {}
    after_by_path: Dict[Path, str] = {}
    risk_reasons: List[str] = []

    for path in paths:
        rel = path.relative_to(project_root).as_posix()
        if allowed and rel not in allowed:
            risk_reasons.append(f"目标文件不在本轮上下文中：{rel}")
        ensure_text_file(path)
        before_by_path[path] = read_text(path)

    for item in edits:
        path = resolve_edit_path(project_root, str(item["path"]))
        original = before_by_path[path]
        edit_type = str(item["type"])
        if edit_type == "delete_file":
            after_by_path[path] = ""
            risk_reasons.append(f"删除文件：{path.relative_to(project_root).as_posix()}")
            continue
        if edit_type == "copy_file":
            source = source_path_for_copy(project_root, item)
            ensure_text_file(source)
            content = preserve_token_exports(original, read_text(source), path)
            after_by_path[path] = content
            risk_reasons.append(
                f"复制文件：{source.relative_to(project_root).as_posix()} -> {path.relative_to(project_root).as_posix()}"
            )
            continue
        if edit_type == "create_file" and path.exists():
            raise EditError(f"create_file 目标已存在：{item['path']}")
        if edit_type in {"write_file", "create_file"}:
            content = preserve_token_exports(original, str(item.get("content", "")), path)
            after_by_path[path] = content
            if edit_type == "write_file" and path.exists():
                risk_reasons.append(f"整文件重写：{path.relative_to(project_root).as_posix()}")
            continue
        search = str(item.get("search", ""))
        replace = str(item.get("replace", ""))
        if not search:
            raise EditError(f"replace_block 缺少 search：{item['path']}")
        count = original.count(search)
        if count == 0:
            raise EditError(f"replace_block 未匹配：{item['path']}")
        if count > 1:
            raise EditError(f"replace_block 匹配多处，拒绝自动修改：{item['path']}")
        after_by_path[path] = preserve_token_exports(original, original.replace(search, replace, 1), path)

    diff_parts: List[str] = []
    changed_files: List[str] = []
    for path in paths:
        before = before_by_path.get(path, "")
        after = after_by_path.get(path, before)
        if before != after:
            rel = path.relative_to(project_root).as_posix()
            changed_files.append(rel)
            diff_parts.append(make_diff(project_root, path, before, after))
            if rel in {"run.sh", "requirements.txt", "test.py"}:
                risk_reasons.append(f"关键入口文件：{rel}")
    diff = "\n".join(diff_parts)
    added, removed = summarize_diff(diff)
    dangerous_patterns = detect_dangerous_delete_code("\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    ))
    for pattern in dangerous_patterns:
        risk_reasons.append(f"危险删除命令/代码：`{pattern}`")
    if len(changed_files) > 3:
        risk_reasons.append(f"一次修改 {len(changed_files)} 个文件")
    if added + removed > 200:
        risk_reasons.append(f"改动较大：+{added}/-{removed}")
    operations = sorted({str(item["type"]) for item in edits})
    requires_single_approval = bool(
        dangerous_patterns
        or any(item["type"] == "delete_file" for item in edits)
        or any("删除文件" in item for item in risk_reasons)
    )
    if dangerous_patterns:
        risk_level = "critical"
    elif any("删除文件" in item or "目标文件不在本轮上下文" in item for item in risk_reasons):
        risk_level = "high"
    elif risk_reasons:
        risk_level = "medium"
    else:
        risk_level = "low"
    return EditPreview(
        summary=summary,
        plan=plan,
        files=changed_files,
        diff=diff,
        added_lines=added,
        removed_lines=removed,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        operations=operations,
        requires_single_approval=requires_single_approval,
    )
