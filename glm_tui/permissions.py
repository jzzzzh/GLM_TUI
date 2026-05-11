from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .editing import EditPreview
from .storage import read_json, write_json


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
REMEMBER_BLOCKED_OPS = {"delete_file"}


@dataclass
class PermissionRule:
    rule_id: str
    operations: List[str]
    files: List[str]
    max_risk: str
    created_at: str
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "operations": self.operations,
            "files": self.files,
            "max_risk": self.max_risk,
            "created_at": self.created_at,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PermissionRule":
        return cls(
            rule_id=str(data.get("rule_id", "")),
            operations=[str(item) for item in data.get("operations", [])],
            files=[str(item) for item in data.get("files", [])],
            max_risk=str(data.get("max_risk", "low")),
            created_at=str(data.get("created_at", "")),
            description=str(data.get("description", "")),
        )


class PermissionStore:
    def __init__(self, root: Path):
        self.path = root / ".glm_tui" / "permissions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list_rules(self) -> List[PermissionRule]:
        data = read_json(self.path, {"rules": []})
        rules = data.get("rules", [])
        return [PermissionRule.from_dict(item) for item in rules if isinstance(item, dict)]

    def save_rules(self, rules: List[PermissionRule]) -> None:
        write_json(self.path, {"rules": [rule.to_dict() for rule in rules]})

    def clear(self) -> None:
        self.save_rules([])

    def can_remember(self, preview: EditPreview) -> Optional[str]:
        if preview.requires_single_approval:
            return "包含删除文件或危险删除代码，只允许本次单独审批，不能记住。"
        blocked = sorted(set(preview.operations) & REMEMBER_BLOCKED_OPS)
        if blocked:
            return f"包含不可记住的操作：{', '.join(blocked)}。"
        if RISK_ORDER.get(preview.risk_level, 99) > RISK_ORDER["medium"]:
            return f"风险等级为 {preview.risk_level}，不能记住。"
        if not preview.files:
            return "没有文件变化，不能记住。"
        return None

    def remember(self, preview: EditPreview) -> PermissionRule:
        reason = self.can_remember(preview)
        if reason:
            raise ValueError(reason)
        rule = PermissionRule(
            rule_id="perm_" + uuid4().hex[:8],
            operations=sorted(preview.operations),
            files=sorted(preview.files),
            max_risk=preview.risk_level,
            created_at=datetime.now().replace(microsecond=0).isoformat(),
            description=preview.summary,
        )
        rules = [item for item in self.list_rules() if not same_scope(item, rule)]
        rules.append(rule)
        self.save_rules(rules[-50:])
        return rule

    def matching_rule(self, preview: EditPreview) -> Optional[PermissionRule]:
        if self.can_remember(preview):
            return None
        operations = sorted(preview.operations)
        files = sorted(preview.files)
        risk = RISK_ORDER.get(preview.risk_level, 99)
        for rule in reversed(self.list_rules()):
            if sorted(rule.operations) != operations:
                continue
            if sorted(rule.files) != files:
                continue
            if risk <= RISK_ORDER.get(rule.max_risk, -1):
                return rule
        return None


def same_scope(left: PermissionRule, right: PermissionRule) -> bool:
    return sorted(left.operations) == sorted(right.operations) and sorted(left.files) == sorted(right.files)
