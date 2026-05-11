from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


@dataclass
class Checkpoint:
    change_id: str
    summary: str
    created_at: str
    files: List[Dict[str, Any]]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CheckpointStore:
    def __init__(self, root: Path):
        self.root = root / ".glm_tui" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, paths: Iterable[Path], project_root: Path, summary: str) -> Checkpoint:
        project_root = project_root.resolve()
        change_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        folder = self.root / change_id
        originals = folder / "originals"
        originals.mkdir(parents=True, exist_ok=True)
        files: List[Dict[str, Any]] = []
        for path in sorted({item.resolve() for item in paths}):
            rel = path.relative_to(project_root).as_posix()
            original_path = originals / rel
            original_path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raw = path.read_bytes()
                original_path.write_bytes(raw)
                files.append(
                    {
                        "path": rel,
                        "existed": True,
                        "sha256": sha256_bytes(raw),
                        "original": str(original_path.relative_to(folder)),
                    }
                )
            else:
                files.append(
                    {
                        "path": rel,
                        "existed": False,
                        "sha256": None,
                        "original": None,
                    }
                )
        checkpoint = Checkpoint(
            change_id=change_id,
            summary=summary,
            created_at=datetime.now().replace(microsecond=0).isoformat(),
            files=files,
        )
        (folder / "manifest.json").write_text(
            json.dumps(checkpoint.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return checkpoint

    def load(self, change_id: str) -> Optional[Checkpoint]:
        path = self.root / change_id / "manifest.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return Checkpoint(
            change_id=str(data.get("change_id", change_id)),
            summary=str(data.get("summary", "")),
            created_at=str(data.get("created_at", "")),
            files=list(data.get("files", [])),
        )

    def list(self) -> List[Checkpoint]:
        result: List[Checkpoint] = []
        for manifest in sorted(self.root.glob("*/manifest.json"), reverse=True):
            item = self.load(manifest.parent.name)
            if item:
                result.append(item)
        return result

    def latest(self) -> Optional[Checkpoint]:
        items = self.list()
        return items[0] if items else None

    def restore(self, change_id: str, project_root: Path) -> List[str]:
        project_root = project_root.resolve()
        checkpoint = self.load(change_id)
        if checkpoint is None:
            raise FileNotFoundError(f"找不到 checkpoint：{change_id}")
        folder = self.root / change_id
        restored: List[str] = []
        for item in checkpoint.files:
            rel = str(item["path"])
            target = (project_root / rel).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.get("existed"):
                original = folder / str(item["original"])
                shutil.copyfile(original, target)
            elif target.exists():
                target.unlink()
            restored.append(rel)
        return restored
