from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class ParsedCommand:
    name: str
    args: str
    raw: str


@dataclass
class CommandSuggestion:
    command: str
    usage: str
    description: str
    completion: str


COMMAND_HELP = [
    ("/help", "显示帮助和快捷键。"),
    ("/model [模型名]", "查看或切换模型，如 `/model glm-4.7-flash`。"),
    ("/temp [0-2]", "设置温度，如 `/temp 0.8`。"),
    ("/stream on|off", "开启或关闭流式输出。"),
    ("/thinking on|off|auto", "设置 GLM-4.7 系列思考开关。"),
    ("/mode ask|code|auto", "切换聊天/代码代理/自动识别模式。"),
    ("/approval strict|balanced", "设置代码修改审批策略。"),
    ("/approve [remember]", "同意当前变更；加 remember 可记住同路径同操作的低/中风险相似任务。"),
    ("/reject", "拒绝并丢弃当前待审批代码变更。"),
    ("/rag on|off", "开启或关闭本地记忆 RAG。"),
    ("/new [名称]", "新建会话。"),
    ("/clear", "清空当前会话消息，保留长期记忆。"),
    ("/memory", "查看长期记忆。"),
    ("/memory sync", "立即整理未总结对话并更新记忆。"),
    ("/memory search 关键词", "搜索记忆片段。"),
    ("/memory detail id", "查看记忆片段链接的原始对话。"),
    ("/remember 键=值", "写入长期记忆。"),
    ("/forget 键", "删除长期记忆。"),
    ("/name [名字]", "查看或修改助手名字。"),
    ("/user [名字]", "查看或修改用户名字。"),
    ("/persona [描述]", "查看或修改助手回答风格。"),
    ("/search 关键词", "联网搜索并让模型基于搜索结果回答。"),
    ("/image 提示词", "提交 GLM-Image 异步图片生成任务。"),
    ("/video 提示词", "提交 CogVideoX 异步视频生成任务。"),
    ("/result 任务ID", "查询图片或视频异步生成结果。"),
    ("/add 文件路径", "把文件加入只读上下文。"),
    ("/read 文件路径", "预览文件内容。"),
    ("/grep 关键词", "在项目内搜索文本。"),
    ("/skills", "列出中文提示模板。"),
    ("/skill 名称 内容", "套用提示模板并发送。"),
    ("/recap", "生成会话摘要并保存到记忆。"),
    ("/tasks", "显示或刷新任务清单。"),
    ("/changes", "查看最近 AI 代码变更和待审批变更。"),
    ("/undo", "回滚最近一次已应用 AI 代码变更。"),
    ("/export [文件名]", "导出当前对话为 Markdown。"),
    ("/logs [关键词]", "查看或搜索本地对话日志。"),
    ("/sessions", "查看已保存会话。"),
    ("/permissions [clear]", "显示或清空已记住的相似任务授权。"),
    ("/doctor", "检查依赖、API Key、存储目录和当前配置。"),
    ("/theme [名称]", "切换主题：墨绿、深海、纸墨、霓虹。"),
    ("/palette", "显示命令面板。"),
    ("/quit", "保存并退出。"),
]

COMMAND_NAMES = [item[0].split()[0][1:] for item in COMMAND_HELP]


def parse_input(text: str) -> Optional[ParsedCommand]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:]
    if not body:
        return ParsedCommand(name="palette", args="", raw=text)
    parts = body.split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return ParsedCommand(name=name, args=args, raw=text)


def resolve_command_name(name: str) -> Optional[str]:
    if name in COMMAND_NAMES:
        return name
    matches = [item for item in COMMAND_NAMES if item.startswith(name)]
    if len(matches) == 1:
        return matches[0]
    return None


def command_suggestions(text: str, skills: Iterable[str] = ()) -> List[CommandSuggestion]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return []
    body = stripped[1:]
    if body.startswith("skill "):
        query = body.split(" ", 1)[1].strip().split(maxsplit=1)[0].lower()
        result: List[CommandSuggestion] = []
        for skill in skills:
            if not query or skill.lower().startswith(query) or query in skill.lower():
                result.append(
                    CommandSuggestion(
                        command="skill",
                        usage=f"/skill {skill} 内容",
                        description=f"套用「{skill}」提示模板",
                        completion=f"/skill {skill} ",
                    )
                )
        return result[:8]

    query = body.split(maxsplit=1)[0].lower() if body else ""
    result = []
    for usage, description in COMMAND_HELP:
        name = usage.split()[0][1:]
        if not query or name.startswith(query) or query in description.lower():
            result.append(
                CommandSuggestion(
                    command=name,
                    usage=usage,
                    description=description,
                    completion=f"/{name} ",
                )
            )
    return result[:8]


def help_markdown() -> str:
    lines: List[str] = [
        "## 命令面板",
        "",
        "输入 `/` 可打开命令提示；输入普通文字会直接发送给模型。",
        "",
        "| 命令 | 作用 |",
        "| --- | --- |",
    ]
    lines.extend(f"| `{command}` | {description} |" for command, description in COMMAND_HELP)
    lines.extend(
        [
            "",
            "## 快捷键",
            "",
            "- `Ctrl+C`：取消当前生成",
            "- `Ctrl+L`：清屏但保留会话状态",
            "- `Ctrl+T`：切换任务面板",
            "- `Ctrl+O`：切换详细模式",
            "- `/approve`：应用当前待审批代码变更",
            "- `/reject`：丢弃当前待审批代码变更",
            "- `↑/↓`：在命令候选中移动",
            "- `Tab`：补全当前命令或 skill",
            "- `Esc`：关闭弹窗或回到输入框",
        ]
    )
    return "\n".join(lines)
