from __future__ import annotations

from dataclasses import dataclass


CODE_KEYWORDS = (
    "写代码",
    "改代码",
    "修改",
    "实现",
    "新增",
    "添加",
    "修复",
    "重构",
    "删除",
    "生成",
    "补上",
    "接入",
    "实现一下",
    "please implement",
    "implement",
    "fix",
    "refactor",
    "add ",
)

MEMORY_COARSE_KEYWORDS = (
    "之前",
    "以前",
    "记得",
    "记忆",
    "我说过",
    "做过什么",
    "大概",
    "总结",
    "回顾",
)

MEMORY_DETAIL_KEYWORDS = (
    "详细",
    "细节",
    "原话",
    "完整",
    "具体",
    "上次关于",
    "翻看",
    "查一下",
    "log",
    "日志",
)


@dataclass
class Intent:
    kind: str
    use_rag: bool = True


def classify_intent(text: str, mode: str, rag_enabled: bool) -> Intent:
    lowered = text.lower()
    if mode == "code":
        return Intent("code", use_rag=rag_enabled)
    if mode == "ask":
        if rag_enabled and any(item in lowered for item in MEMORY_DETAIL_KEYWORDS):
            return Intent("memory_detail")
        if rag_enabled and any(item in lowered for item in MEMORY_COARSE_KEYWORDS):
            return Intent("memory_summary")
        return Intent("chat", use_rag=rag_enabled)

    if any(item in lowered for item in CODE_KEYWORDS):
        return Intent("code", use_rag=rag_enabled)
    if rag_enabled and any(item in lowered for item in MEMORY_DETAIL_KEYWORDS):
        return Intent("memory_detail")
    if rag_enabled and any(item in lowered for item in MEMORY_COARSE_KEYWORDS):
        return Intent("memory_summary")
    return Intent("chat", use_rag=rag_enabled)
