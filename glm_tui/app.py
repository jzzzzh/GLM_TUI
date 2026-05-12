from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from rich import box
from rich.align import Align
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Input, Static

from .api import GLMAPIError, GLMClient
from .checkpoints import CheckpointStore
from .commands import CommandSuggestion, command_suggestions, help_markdown, parse_input, resolve_command_name
from .context import ContextFile, GrepHit, ProjectContext
from .editing import EditError, EditPreview, apply_edit_plan, candidate_context, preview_edit_plan
from .intent import Intent, classify_intent
from .logs import LogStore, TurnRecord
from .memory import MemoryManager
from .permissions import PermissionStore
from .retrieval import RetrievalIndex
from .state import SUPPORTED_MODELS, ChatSession
from .storage import MemoryStore, SessionStore, format_paths, redact_secrets


TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$")
ASSISTANT_NAME_PATTERNS = (
    re.compile(r"(?:把)?(?:你的名字|你名字|助手名字)(?:改成|改为|设为|设置为|叫做|叫)\s*([^，,。.!！?？\n]+)"),
    re.compile(r"(?:你以后|以后你|从现在起你)(?:就)?叫\s*([^，,。.!！?？\n]+)"),
    re.compile(r"以后叫你\s*([^，,。.!！?？\n]+)"),
    re.compile(r"^你叫\s*([^，,。.!！?？\n]+)"),
)
USER_NAME_PATTERNS = (
    re.compile(r"(?:我的名字|我名字)(?:是|叫|改成|改为|设为|设置为)\s*([^，,。.!！?？\n]+)"),
    re.compile(r"(?:以后|以后请|请)?叫我\s*([^，,。.!！?？\n]+)"),
)
PERSONA_PATTERNS = (
    re.compile(r"(?:回答风格|你的风格|个性|性格)(?:改成|改为|设为|设置为|是)\s*([^，,。.!！?？\n]+)"),
    re.compile(r"(?:以后|之后)(?:请)?(?:用|以)\s*([^，,。.!！?？\n]+?)(?:的)?(?:风格|语气)(?:回答|说话)?"),
    re.compile(r"(?:个性化|偏好)(?:设置|修改|改成|改为|设为)\s*([^，,。.!！?？\n]+)"),
)
THEMES = ["墨绿", "深海", "纸墨", "霓虹"]


class MessageBubble(Static):
    def __init__(self, role: str, body: str, assistant_name: str = "GLM"):
        super().__init__()
        self.role = role
        self.body = body
        self.assistant_name = assistant_name
        self.add_class(f"role-{role}")

    def on_mount(self) -> None:
        self.set_body(self.body)

    def set_body(self, body: str) -> None:
        self.body = body
        title = {"user": "◇ 你", "assistant": f"✦ {self.assistant_name}", "system": "◆ 系统"}.get(self.role, self.role)
        border = {"user": "#70d6ff", "assistant": "#8cffc1", "system": "#ffd166"}.get(self.role, "white")
        self.update(
            Panel(
                Markdown(body or " "),
                title=title,
                border_style=border,
                box=box.ROUNDED,
                padding=(0, 1),
                expand=True,
            )
        )


class GLMTuiApp(App[None]):
    TITLE = "GLM 中文 TUI 助手"
    SUB_TITLE = "Claude Code 风格 · 只读项目上下文 · 本地记忆"

    BINDINGS = [
        ("ctrl+c", "cancel_generation", "取消生成"),
        ("ctrl+l", "clear_screen", "清屏"),
        ("ctrl+t", "toggle_tasks", "任务面板"),
        ("ctrl+o", "toggle_detail", "详细模式"),
        ("up", "command_prev", "上一项"),
        ("down", "command_next", "下一项"),
        ("tab", "complete_command", "补全"),
        ("escape", "focus_input", "回到输入"),
    ]

    CSS = """
    Screen {
        background: #07110e;
        color: #d9f4e8;
        layout: vertical;
    }

    #workspace {
        height: 1fr;
    }

    #left {
        width: 31;
        min-width: 24;
        border: round #63e6a6;
        padding: 1;
        background: #0b1d17;
    }

    #chat {
        width: 1fr;
        padding: 1 2;
        background: #06130f;
    }

    #right {
        width: 34;
        min-width: 26;
        border: round #7ec8ff;
        padding: 1;
        background: #071724;
    }

    #mascot-line {
        height: 5;
        border: round #8cffc1;
        padding: 0 1;
        background: #081b15;
        color: #d7ffe9;
        margin: 0 1;
    }

    #status-line {
        height: 3;
        border: round #3f8f6a;
        padding: 0 1;
        background: #071913;
        margin: 0 1;
    }

    #activity-line {
        height: 3;
        border: round #ffd166;
        padding: 0 1;
        background: #102017;
        color: #bfffe0;
        margin: 0 1;
    }

    #command-hints {
        height: auto;
        max-height: 9;
        border: round #64d2ff;
        padding: 0 1;
        background: #071724;
        color: #d9f4e8;
        margin: 0 1;
    }

    #composer {
        height: 3;
        border: round #9cffcb;
        background: #061811;
        margin: 0 1 1 1;
    }

    MessageBubble {
        margin: 0 0 1 0;
    }

    .role-user {
        margin-left: 6;
    }

    .role-assistant {
        margin-right: 6;
    }

    .role-system {
        margin-left: 3;
        margin-right: 3;
    }

    .hidden {
        display: none;
    }

    .theme-paper {
        background: #f5f0e6;
        color: #1f2933;
    }

    .theme-paper #left,
    .theme-paper #chat,
    .theme-paper #right,
    .theme-paper #mascot-line,
    .theme-paper #activity-line,
    .theme-paper #status-line,
    .theme-paper #command-hints,
    .theme-paper #composer {
        background: #fbf8f1;
        color: #1f2933;
    }

    .theme-neon #left {
        border: round #ff7be7;
    }

    .theme-neon #right {
        border: round #6be7ff;
    }

    .theme-neon #mascot-line {
        border: round #ffe066;
    }

    .pending {
        color: #ffd166;
    }
    """

    def __init__(self, root: Optional[Path] = None):
        super().__init__()
        self.root = (root or Path.cwd()).resolve()
        self.memory = MemoryStore(self.root)
        self.sessions = SessionStore(self.root)
        latest = self.sessions.load_latest()
        self.session = latest or ChatSession()
        if latest is None:
            last_model = self.memory.data.get("last_model")
            if isinstance(last_model, str) and last_model:
                self.session.model = last_model
        self.project = ProjectContext(self.root)
        self.client = GLMClient()
        self.checkpoints = CheckpointStore(self.root)
        self.logs = LogStore(self.root)
        self.retrieval = RetrievalIndex(self.root)
        self.memory_manager = MemoryManager(self.root)
        self.permissions = PermissionStore(self.root)
        self.context_blocks: Dict[str, ContextFile] = {}
        self.search_hits: List[GrepHit] = []
        self.tasks: List[str] = []
        self.current_task: Optional[asyncio.Task[None]] = None
        self.verbose = False
        self.theme_name = "墨绿"
        self.command_matches: List[CommandSuggestion] = []
        self.command_index = 0
        self.pending_edit: Optional[EditPreview] = None
        self.pending_user_input = ""
        self.pending_intent = "code"
        self.memory_sync_task: Optional[asyncio.Task[None]] = None
        self.activity_frames = ["✦", "✧", "◆", "◇", "◈", "◇", "◆", "✧"]
        self.mascot_frames = ["(•‿•)", "(•◡•)", "(•ᴗ•)", "(•ᵕ•)", "(•◡•)"]
        self.loading_phrases = ["整理上下文", "读取记忆", "守护写入", "等待指令", "检查风险"]
        self.activity_index = 0
        self.activity_status = "待命"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="mascot-line")
        with Horizontal(id="workspace"):
            with Vertical(id="left"):
                yield Static(id="left-panel")
            with VerticalScroll(id="chat"):
                pass
            with Vertical(id="right"):
                yield Static(id="right-panel")
        yield Static(id="activity-line")
        yield Static(id="status-line")
        yield Static(id="command-hints", classes="hidden")
        yield Input(
            placeholder="输入问题，或输入 /help 查看命令。支持 @test.py 引用文件。",
            id="composer",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._render_existing_session()
        self._refresh_sidebars()
        self._refresh_status("就绪")
        self._refresh_mascot()
        self.query_one("#composer", Input).focus()
        self.retrieval.rebuild_turns(self.logs.read_all())
        self._schedule_memory_sync(show_empty=False)
        self.set_interval(60, self._autosave)
        self.set_interval(0.25, self._tick_activity)

    async def on_unmount(self) -> None:
        self._save_state()

    def _save_state(self) -> None:
        self.sessions.save(self.session)

    def _autosave(self) -> None:
        self._save_state()
        self._refresh_status("已自动保存")

    def _schedule_memory_sync(self, show_empty: bool) -> None:
        if self.memory_sync_task and not self.memory_sync_task.done():
            return
        self.memory_sync_task = asyncio.create_task(self._sync_memory_background(show_empty=show_empty))

    async def _ensure_memory_current(self) -> None:
        if self.memory_sync_task and not self.memory_sync_task.done():
            await self.memory_sync_task
            return
        await self._sync_memory_background(show_empty=False)

    async def _animate_until_done(
        self,
        task: asyncio.Task[None],
        bubble: MessageBubble,
        steps: List[str],
    ) -> None:
        index = 0
        while not task.done():
            marker = self.activity_frames[index % len(self.activity_frames)]
            step = steps[index % len(steps)]
            bubble.set_body(f"{marker} {step}中，请稍候...")
            self._refresh_status(step + "中")
            index += 1
            await asyncio.sleep(0.25)
        await task

    async def _render_existing_session(self) -> None:
        if self.session.messages:
            await self._notice(f"已恢复会话：{self.session.name}（{len(self.session.messages)} 条消息）")
            for message in self.session.messages[-16:]:
                await self._mount_message(message.role, message.content)
        else:
            await self._notice("欢迎使用 GLM 中文 TUI。输入 `/help` 查看命令，输入普通文字开始对话。")

    async def _mount_message(self, role: str, body: str) -> MessageBubble:
        bubble = MessageBubble(role, redact_secrets(body), self.memory.get_profile("assistant_name", "GLM"))
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(bubble)
        chat.scroll_end(animate=False)
        return bubble

    async def _notice(self, body: str) -> MessageBubble:
        return await self._mount_message("system", body)

    @on(Input.Submitted, "#composer")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        matches = list(self.command_matches)
        index = self.command_index
        event.input.value = ""
        self._hide_command_hints()
        if not text:
            return
        command = parse_input(text)
        if command:
            resolved = resolve_command_name(command.name)
            if resolved:
                command.name = resolved
            elif matches:
                command.name = matches[index].command
            await self._run_command(command.name, command.args)
            return
        self._start_chat_task(text)

    @on(Input.Changed, "#composer")
    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_command_hints(event.value)

    def _start_chat_task(self, text: str) -> None:
        if self.current_task and not self.current_task.done():
            asyncio.create_task(self._async_notice("当前正在生成。请先按 Ctrl+C 取消，或等待完成。"))
            return
        self.current_task = asyncio.create_task(self._send_user_message(text))

    async def _async_notice(self, body: str) -> None:
        await self._notice(body)

    def _update_command_hints(self, value: str) -> None:
        try:
            hints = self.query_one("#command-hints", Static)
        except NoMatches:
            return
        if not value.strip().startswith("/"):
            self._hide_command_hints()
            return
        self.command_matches = command_suggestions(value, self.memory.list_skills())
        self.command_index = min(self.command_index, max(len(self.command_matches) - 1, 0))
        if not self.command_matches:
            hints.update("没有匹配命令。输入 `/help` 查看全部命令。")
            hints.remove_class("hidden")
            return
        lines = ["命令候选：↑/↓ 选择，Tab 补全，Enter 可直接执行当前匹配"]
        for index, suggestion in enumerate(self.command_matches):
            marker = "▶" if index == self.command_index else " "
            lines.append(f"{marker} {suggestion.usage}  —  {suggestion.description}")
        hints.update("\n".join(lines))
        hints.remove_class("hidden")

    def _hide_command_hints(self) -> None:
        self.command_matches = []
        self.command_index = 0
        try:
            hints = self.query_one("#command-hints", Static)
        except NoMatches:
            return
        hints.update("")
        hints.add_class("hidden")

    def _move_command_selection(self, delta: int) -> bool:
        if not self.command_matches:
            return False
        self.command_index = (self.command_index + delta) % len(self.command_matches)
        composer = self.query_one("#composer", Input)
        self._update_command_hints(composer.value)
        return True

    def _complete_selected_command(self) -> bool:
        if not self.command_matches:
            return False
        composer = self.query_one("#composer", Input)
        suggestion = self.command_matches[self.command_index]
        current = composer.value
        if current.startswith("/skill "):
            composer.value = suggestion.completion
        else:
            body = current[1:] if current.startswith("/") else current
            rest = ""
            if " " in body:
                rest = body.split(" ", 1)[1]
            composer.value = suggestion.completion + rest
        composer.cursor_position = len(composer.value)
        self._update_command_hints(composer.value)
        return True

    async def _run_command(self, name: str, args: str) -> None:
        handlers = {
            "help": self._cmd_help,
            "palette": self._cmd_help,
            "model": self._cmd_model,
            "temp": self._cmd_temp,
            "stream": self._cmd_stream,
            "thinking": self._cmd_thinking,
            "mode": self._cmd_mode,
            "approval": self._cmd_approval,
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
            "rag": self._cmd_rag,
            "new": self._cmd_new,
            "clear": self._cmd_clear,
            "memory": self._cmd_memory,
            "remember": self._cmd_remember,
            "forget": self._cmd_forget,
            "name": self._cmd_name,
            "user": self._cmd_user,
            "persona": self._cmd_persona,
            "add": self._cmd_add,
            "read": self._cmd_read,
            "grep": self._cmd_grep,
            "skills": self._cmd_skills,
            "skill": self._cmd_skill,
            "recap": self._cmd_recap,
            "tasks": self._cmd_tasks,
            "changes": self._cmd_changes,
            "undo": self._cmd_undo,
            "export": self._cmd_export,
            "logs": self._cmd_logs,
            "sessions": self._cmd_sessions,
            "doctor": self._cmd_doctor,
            "permissions": self._cmd_permissions,
            "theme": self._cmd_theme,
            "quit": self._cmd_quit,
            "exit": self._cmd_quit,
            "q": self._cmd_quit,
        }
        handler = handlers.get(name)
        if not handler:
            await self._notice(f"未知命令：`/{name}`。输入 `/help` 查看可用命令。")
            return
        await handler(args)

    async def _cmd_help(self, _: str) -> None:
        await self._notice(help_markdown())

    async def _cmd_model(self, args: str) -> None:
        if not args:
            models = "\n".join(
                f"- {'✓ ' if item == self.session.model else ''}`{item}`"
                for item in SUPPORTED_MODELS
            )
            await self._notice(f"## 可选模型\n\n{models}\n\n也可以输入自定义模型名：`/model your-model`。")
            return
        self.session.model = args.strip()
        self.memory.set_last_model(self.session.model)
        self.sessions.save(self.session)
        self._refresh_sidebars()
        self._refresh_status("模型已切换")
        marker = "" if self.session.model in SUPPORTED_MODELS else "（自定义模型名）"
        await self._notice(f"已切换模型为 `{self.session.model}` {marker}")

    async def _cmd_temp(self, args: str) -> None:
        try:
            value = float(args)
        except ValueError:
            await self._notice(f"当前温度：`{self.session.temperature}`。用法：`/temp 0.8`")
            return
        if not 0 <= value <= 2:
            await self._notice("温度范围建议为 0 到 2。")
            return
        self.session.temperature = value
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"温度已设置为 `{value}`。")

    async def _cmd_stream(self, args: str) -> None:
        value = args.lower()
        if value not in {"on", "off"}:
            await self._notice(f"当前流式输出：`{'on' if self.session.stream else 'off'}`。用法：`/stream on|off`")
            return
        self.session.stream = value == "on"
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"流式输出已设置为 `{'on' if self.session.stream else 'off'}`。")

    async def _cmd_thinking(self, args: str) -> None:
        value = args.lower()
        if value not in {"on", "off", "auto"}:
            current = "auto" if self.session.thinking is None else str(self.session.thinking).lower()
            await self._notice(f"当前思考模式：`{current}`。用法：`/thinking on|off|auto`")
            return
        self.session.thinking = None if value == "auto" else value == "on"
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"思考模式已设置为 `{value}`。仅在 GLM-4.7 系列请求中注入参数。")

    async def _cmd_mode(self, args: str) -> None:
        value = args.lower().strip()
        if value not in {"ask", "code", "auto"}:
            await self._notice(
                f"当前模式：`{self.session.mode}`。\n\n"
                "- `ask`：只聊天，不自动改文件\n"
                "- `code`：每条普通输入都按代码修改处理\n"
                "- `auto`：自动识别聊天或代码修改"
            )
            return
        self.session.mode = value
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"模式已切换为 `{value}`。")

    async def _cmd_approval(self, args: str) -> None:
        value = args.lower().strip()
        if value not in {"strict", "balanced"}:
            await self._notice(
                f"当前审批策略：`{self.session.approval_policy}`。\n\n"
                "- `strict`：所有代码变更都必须 `/approve` 后才写文件。\n"
                "- `balanced`：低风险变更仍会先预览；高风险变更必须审批。本项目当前仍默认建议 strict。"
            )
            return
        self.session.approval_policy = value
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"审批策略已设置为 `{value}`。")

    async def _cmd_approve(self, args: str) -> None:
        if not self.pending_edit:
            await self._notice("当前没有待审批代码变更。")
            return
        remember = args.strip().lower() in {"remember", "similar", "same", "记住"}
        await self._apply_pending_edit(remember=remember)

    async def _cmd_reject(self, _: str) -> None:
        if not self.pending_edit:
            await self._notice("当前没有待审批代码变更。")
            return
        summary = self.pending_edit.summary
        self.pending_edit = None
        self.pending_user_input = ""
        self._refresh_sidebars()
        self._refresh_status("已拒绝变更")
        self._refresh_mascot()
        await self._notice(f"已丢弃待审批变更：{summary}")

    async def _cmd_rag(self, args: str) -> None:
        value = args.lower().strip()
        if value not in {"on", "off"}:
            await self._notice(f"当前 RAG：`{'on' if self.session.rag_enabled else 'off'}`。用法：`/rag on|off`")
            return
        self.session.rag_enabled = value == "on"
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"本地记忆 RAG 已设置为 `{'on' if self.session.rag_enabled else 'off'}`。")

    async def _cmd_new(self, args: str) -> None:
        self.sessions.save(self.session)
        name = args.strip() or "新会话"
        old_model = self.session.model
        old_temp = self.session.temperature
        self.session = ChatSession(name=name, model=old_model, temperature=old_temp)
        self.context_blocks.clear()
        self.search_hits.clear()
        self.tasks.clear()
        await self._clear_chat_view()
        await self._notice(f"已创建会话：{name}")
        self._refresh_sidebars()

    async def _cmd_clear(self, _: str) -> None:
        self.session.clear_messages()
        self.sessions.save(self.session)
        await self._clear_chat_view()
        await self._notice("当前会话消息已清空，长期记忆和上下文文件保留。")
        self._refresh_sidebars()

    async def _cmd_memory(self, args: str) -> None:
        parts = args.split(maxsplit=1)
        subcommand = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if subcommand == "sync":
            await self._sync_memory_background(show_empty=True)
            return
        if subcommand == "search":
            await self._cmd_memory_search(rest)
            return
        if subcommand == "detail":
            await self._cmd_memory_detail(rest)
            return
        long_summary = self.memory_manager.summary_text()
        body = self.memory.display()
        if long_summary:
            body += "\n\n## 自动总结\n\n" + long_summary
        await self._notice("## 长期记忆\n\n" + body)

    async def _cmd_memory_search(self, query: str) -> None:
        if not query:
            await self._notice("用法：`/memory search 关键词`。")
            return
        hits = self.retrieval.search_segments(query, limit=10)
        if not hits:
            await self._notice(f"没有找到记忆片段：`{query}`。")
            return
        lines = [f"- `[mem:{hit.item_id}]` {hit.content[:240]}" for hit in hits]
        await self._notice("## 记忆搜索结果\n\n" + "\n".join(lines))

    async def _cmd_memory_detail(self, memory_id: str) -> None:
        memory_id = memory_id.strip().removeprefix("mem:")
        if not memory_id:
            await self._notice("用法：`/memory detail memory_id`。")
            return
        segment = self.retrieval.get_segment(memory_id)
        if not segment:
            await self._notice(f"没有找到记忆片段：`{memory_id}`。")
            return
        source_ids = []
        try:
            import json

            source_ids = json.loads(segment.get("source_turn_ids") or "[]")
        except Exception:
            source_ids = []
        turns = [self.logs.get_turn(str(turn_id)) for turn_id in source_ids]
        turn_text = "\n\n".join(
            f"### [log:{turn.session_id}:{turn.turn_id}]\n用户：{turn.user_input}\n\n助手：{turn.assistant_output[:1200]}"
            for turn in turns
            if turn
        )
        await self._notice(
            f"## 记忆片段 `{memory_id}`\n\n{segment.get('content', '')}\n\n"
            f"## 来源对话\n\n{turn_text or '没有可用来源。'}"
        )

    async def _cmd_remember(self, args: str) -> None:
        if "=" not in args:
            await self._notice("用法：`/remember 键=值`，例如 `/remember 风格=回答尽量简洁`。")
            return
        key, value = args.split("=", 1)
        try:
            self.memory.remember(key, value)
        except ValueError as exc:
            await self._notice(str(exc))
            return
        self._refresh_sidebars()
        await self._notice(f"已写入记忆：`{key.strip()}`。")

    async def _cmd_forget(self, args: str) -> None:
        if not args:
            await self._notice("用法：`/forget 键`。")
            return
        removed = self.memory.forget(args)
        self._refresh_sidebars()
        await self._notice("已删除该记忆。" if removed else "没有找到这个记忆键。")

    async def _cmd_name(self, args: str) -> None:
        name = args.strip()
        if not name:
            current = self.memory.get_profile("assistant_name", "GLM")
            await self._notice(f"当前助手名字：`{current}`。用法：`/name 小智`")
            return
        self.memory.set_profile("assistant_name", name)
        self.memory.remember("助手名字", name)
        self._refresh_sidebars()
        await self._notice(f"已把助手名字改为：`{name}`。以后我会按这个名字自称。")

    async def _cmd_user(self, args: str) -> None:
        name = args.strip()
        if not name:
            current = self.memory.get_profile("user_name", "")
            await self._notice(f"当前用户名字：`{current or '未设置'}`。用法：`/user 张三`")
            return
        self.memory.set_profile("user_name", name)
        self.memory.remember("用户名字", name)
        self._refresh_sidebars()
        await self._notice(f"已记住用户名字：`{name}`。")

    async def _cmd_persona(self, args: str) -> None:
        persona = args.strip()
        if not persona:
            current = self.memory.get_profile("personality", "")
            await self._notice(f"当前回答风格：`{current or '未设置'}`。用法：`/persona 简洁、直接、少废话`")
            return
        self.memory.set_profile("personality", persona)
        self.memory.remember("回答风格", persona)
        self._refresh_sidebars()
        await self._notice(f"已更新回答风格：`{persona}`。")

    async def _cmd_add(self, args: str) -> None:
        if not args:
            await self._notice("用法：`/add test.py`。")
            return
        await self._add_context_file(args)

    async def _cmd_read(self, args: str) -> None:
        if not args:
            await self._notice("用法：`/read test.py`。")
            return
        try:
            item = self.project.read_file(args)
        except (OSError, ValueError) as exc:
            await self._notice(str(exc))
            return
        preview = item.content[:6000]
        suffix = "\n\n（预览已截断）" if len(item.content) > len(preview) else ""
        await self._notice(f"## {item.path}\n\n```text\n{preview}\n```{suffix}")

    async def _cmd_grep(self, args: str) -> None:
        if not args:
            await self._notice("用法：`/grep 关键词`。")
            return
        try:
            self.search_hits = self.project.grep(args)
        except ValueError as exc:
            await self._notice(str(exc))
            return
        self._refresh_sidebars()
        if not self.search_hits:
            await self._notice(f"没有找到：`{args}`。")
            return
        lines = [f"- `{hit.path}:{hit.line_no}` {hit.line}" for hit in self.search_hits[:20]]
        await self._notice("## 搜索结果\n\n" + "\n".join(lines))

    async def _cmd_skills(self, _: str) -> None:
        skills = self.memory.list_skills()
        body = "\n".join(f"- `{item}`" for item in skills) or "暂无技能模板。"
        await self._notice("## 中文提示模板\n\n" + body + "\n\n用法：`/skill 代码解释 @test.py 这段代码做什么`")

    async def _cmd_skill(self, args: str) -> None:
        if not args:
            await self._cmd_skills("")
            return
        parts = args.split(maxsplit=1)
        name = parts[0]
        user_input = parts[1] if len(parts) > 1 else ""
        template = self.memory.load_skill(name)
        if template is None:
            matches = [skill for skill in self.memory.list_skills() if skill.startswith(name)]
            if len(matches) == 1:
                name = matches[0]
                template = self.memory.load_skill(name)
            elif len(matches) > 1:
                body = "\n".join(f"- `{item}`" for item in matches)
                await self._notice(f"匹配到多个技能模板，请再多输入几个字：\n\n{body}")
                return
        if template is None:
            await self._notice(f"没有找到技能模板：`{name}`。输入 `/skills` 查看。")
            return
        if not user_input:
            await self._notice(f"## {name}\n\n```text\n{template}\n```")
            return
        self._start_chat_task(template.replace("{input}", user_input))

    async def _cmd_recap(self, _: str) -> None:
        if not self.session.messages:
            await self._notice("当前会话还没有可总结的内容。")
            return
        if self.current_task and not self.current_task.done():
            await self._notice("当前正在生成，稍后再执行 `/recap`。")
            return
        self.current_task = asyncio.create_task(self._generate_recap())

    async def _cmd_tasks(self, _: str) -> None:
        self._extract_tasks_from_messages()
        self._refresh_sidebars()
        body = "\n".join(self.tasks) if self.tasks else "暂无任务。助手回答中的 `- [ ]` 会自动进入这里。"
        await self._notice("## 任务清单\n\n" + body)

    async def _cmd_changes(self, _: str) -> None:
        changes = self.checkpoints.list()[:10]
        sections = []
        if self.pending_edit:
            sections.append(
                "## 待审批变更\n\n"
                f"- 摘要：{self.pending_edit.summary}\n"
                f"- 风险：`{self.pending_edit.risk_level}`\n"
                f"- 文件：{', '.join(self.pending_edit.files) or '-'}\n"
                f"- 统计：+{self.pending_edit.added_lines}/-{self.pending_edit.removed_lines}\n\n"
                "输入 `/approve` 应用，或 `/reject` 丢弃。"
            )
        if not changes and not sections:
            await self._notice("暂无 AI 自动代码变更。")
            return
        if changes:
            lines = [
                f"- `{change.change_id}` {change.summary}（{len(change.files)} 个文件，{change.created_at}）"
                for change in changes
            ]
            sections.append("## 最近已应用变更\n\n" + "\n".join(lines) + "\n\n使用 `/undo` 回滚最近一次。")
        await self._notice("\n\n".join(sections))

    async def _cmd_undo(self, args: str) -> None:
        change_id = args.strip() or self.session.last_change_id
        if not change_id:
            latest = self.checkpoints.latest()
            change_id = latest.change_id if latest else ""
        if not change_id:
            await self._notice("暂无可回滚的 AI 变更。")
            return
        try:
            restored = self.checkpoints.restore(change_id, self.root)
        except OSError as exc:
            await self._notice(f"回滚失败：{exc}")
            return
        self.session.last_change_id = None
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice("## 已回滚\n\n" + "\n".join(f"- `{path}`" for path in restored))

    async def _cmd_export(self, args: str) -> None:
        path = self.sessions.export_markdown(self.session, args or None)
        await self._notice(f"已导出：`{path}`。敏感 token 已遮蔽。")

    async def _cmd_logs(self, args: str) -> None:
        records = self.logs.search(args, limit=10) if args else self.logs.read_all(limit=10)
        if not records:
            await self._notice("暂无本地对话日志。")
            return
        blocks = [
            f"### [log:{record.session_id}:{record.turn_id}]\n"
            f"- 时间：{record.created_at}\n"
            f"- 意图：{record.intent}\n"
            f"- 变更：`{record.change_id or '-'}`\n\n"
            f"用户：{record.user_input}\n\n助手：{record.assistant_output[:800]}"
            for record in records
        ]
        await self._notice("## 对话日志\n\n" + "\n\n".join(blocks))

    async def _cmd_sessions(self, _: str) -> None:
        await self._notice("## 已保存会话\n\n" + format_paths(self.sessions.list_sessions()[:20]))

    async def _cmd_doctor(self, _: str) -> None:
        checks = [
            ("API Key", "已设置" if os.getenv("ZHIPUAI_API_KEY") else "缺失"),
            ("工作目录", str(self.root)),
            ("存储目录", str(self.sessions.root)),
            ("当前模型", self.session.model),
            ("当前模式", self.session.mode),
            ("审批策略", self.session.approval_policy),
            ("待审批变更", self.pending_edit.summary if self.pending_edit else "无"),
            ("本地 RAG", "开启" if self.session.rag_enabled else "关闭"),
            ("流式输出", "开启" if self.session.stream else "关闭"),
            ("长期记忆", "已加载"),
            ("对话日志", str(self.logs.turns_path)),
            ("只读上下文", f"{len(self.context_blocks)} 个文件"),
        ]
        body = "\n".join(f"- **{key}**：{value}" for key, value in checks)
        await self._notice("## Doctor\n\n" + body)

    async def _cmd_permissions(self, args: str) -> None:
        if args.strip().lower() in {"clear", "reset", "清空"}:
            self.permissions.clear()
            await self._notice("已清空所有记住的相似任务授权。")
            return
        rules = self.permissions.list_rules()
        remembered = "\n".join(
            f"- `{rule.rule_id}` ops={','.join(rule.operations)} files={','.join(rule.files)} risk<={rule.max_risk}"
            for rule in rules[-10:]
        ) or "暂无。"
        await self._notice(
            "## 自动改代码权限\n\n"
            f"- 当前审批策略：`{self.session.approval_policy}`。\n"
            "- 默认不会直接写项目文件；代码代理只生成待审批 diff。\n"
            "- 只有输入 `/approve` 后才会应用当前待审批变更。\n"
            "- 输入 `/approve remember` 会记住当前低/中风险相似任务；作用域绑定操作类型和目标文件，不会扩展成所有 cp/rm。\n"
            "- 输入 `/reject` 会丢弃待审批变更。\n"
            "- `rm`、删除文件、递归删除、`shutil.rmtree`、`os.remove`、`unlink` 等危险删除代码永远只允许单次审批，不能记住。\n"
            "- 拒绝路径：`.git/`、`.venv/`、`.glm_tui/`、`node_modules/`、`__pycache__/`、`.idea/`。\n"
            "- 拒绝内容：二进制文件、超大文件、项目目录外路径。\n"
            "- 每次应用前都会创建 checkpoint，可用 `/undo` 回滚最近变更。\n"
            "- 发送给模型和写入日志前会遮蔽 API key 与 Bearer token。\n"
            "- 修改 `run.sh` 时会保留原始 `ZHIPUAI_API_KEY` export 行。\n\n"
            "## 已记住的相似任务授权\n\n"
            f"{remembered}\n\n"
            "使用 `/permissions clear` 可清空这些授权。"
        )

    async def _cmd_theme(self, args: str) -> None:
        name = args.strip()
        if not name:
            index = THEMES.index(self.theme_name) if self.theme_name in THEMES else 0
            name = THEMES[(index + 1) % len(THEMES)]
        if name not in THEMES:
            await self._notice("可选主题：" + "、".join(THEMES))
            return
        self._apply_theme(name)
        await self._notice(f"主题已切换为：`{name}`。")

    async def _cmd_quit(self, _: str) -> None:
        self._save_state()
        bubble = await self._notice("正在保存会话和整理记忆，请稍候...")
        if self.memory_sync_task and not self.memory_sync_task.done():
            sync_task = self.memory_sync_task
        else:
            sync_task = asyncio.create_task(self._sync_memory_background(show_empty=False))
            self.memory_sync_task = sync_task
        await self._animate_until_done(
            sync_task,
            bubble,
            ["保存会话", "整理记忆", "写入历史", "准备退出"],
        )
        bubble.set_body("保存完成，正在退出...")
        await asyncio.sleep(0.2)
        self.exit()

    async def _add_context_file(self, raw_path: str) -> None:
        try:
            item = self.project.read_file(raw_path)
        except (OSError, ValueError) as exc:
            await self._notice(str(exc))
            return
        self.context_blocks[item.path] = item
        if item.path not in self.session.context_files:
            self.session.context_files.append(item.path)
        self.sessions.save(self.session)
        self._refresh_sidebars()
        await self._notice(f"已加入只读上下文：`{item.path}`。")

    async def _send_user_message(self, text: str) -> None:
        if await self._handle_personalization_message(text):
            return
        assistant_bubble: Optional[MessageBubble] = None
        answer = ""
        intent = classify_intent(text, self.session.mode, self.session.rag_enabled)
        if intent.kind == "code":
            await self._send_code_message(text, intent)
            return
        try:
            self._add_mentions_to_context(text)
            self.session.add_message("user", text)
            self._save_state()
            await self._mount_message("user", text)
            assistant_bubble = await self._mount_message("assistant", "正在思考...")
            self._refresh_status("生成中")

            if intent.kind in {"memory_summary", "memory_detail"}:
                self._refresh_status("同步记忆中")
                await self._ensure_memory_current()

            messages = self.session.api_messages(self._system_prompt(self._rag_context_for(text, intent)))
            if self.session.stream:
                async for event in self.client.complete_stream(
                    model=self.session.model,
                    messages=messages,
                    temperature=self.session.temperature,
                    thinking=self.session.thinking,
                ):
                    if event.kind == "delta":
                        answer += event.content
                        assistant_bubble.set_body(answer + " ▌")
                    elif event.kind == "done" and event.usage:
                        self.session.usage = event.usage
                    await asyncio.sleep(0)
            else:
                event = await self.client.complete_once(
                    model=self.session.model,
                    messages=messages,
                    temperature=self.session.temperature,
                    thinking=self.session.thinking,
                )
                answer = event.content
                if event.usage:
                    self.session.usage = event.usage

            answer = answer.strip() or "（模型没有返回内容）"
            assistant_bubble.set_body(answer)
            self.session.add_message("assistant", answer)
            self._extract_tasks(answer)
            self._save_state()
            record = self._log_turn(text, answer, intent.kind, None)
            self.retrieval.upsert_turn(record)
            self._schedule_memory_sync(show_empty=False)
            self._refresh_sidebars()
            self._refresh_status("完成")
        except asyncio.CancelledError:
            cancelled_answer = (answer or "生成已取消。").rstrip() + "\n\n（已取消）"
            if assistant_bubble:
                assistant_bubble.set_body(cancelled_answer)
            self.session.add_message("assistant", cancelled_answer)
            self._save_state()
            self._refresh_status("已取消")
        except GLMAPIError as exc:
            if assistant_bubble:
                assistant_bubble.set_body("请求失败：\n\n" + redact_secrets(str(exc)))
            self._refresh_status("请求失败")
        finally:
            self.current_task = None
            self.query_one("#composer", Input).focus()

    async def _handle_personalization_message(self, text: str) -> bool:
        updates = self._extract_personalization_updates(text)
        if not updates:
            return False

        self.session.add_message("user", text)
        self._save_state()
        await self._mount_message("user", text)

        lines = []
        assistant_name = updates.get("assistant_name")
        user_name = updates.get("user_name")
        personality = updates.get("personality")
        if assistant_name:
            self.memory.set_profile("assistant_name", assistant_name)
            self.memory.remember("助手名字", assistant_name)
            lines.append(f"- 助手名字已改为：`{assistant_name}`")
        if user_name:
            self.memory.set_profile("user_name", user_name)
            self.memory.remember("用户名字", user_name)
            lines.append(f"- 用户名字已记住：`{user_name}`")
        if personality:
            self.memory.set_profile("personality", personality)
            self.memory.remember("回答风格", personality)
            lines.append(f"- 回答风格已更新：`{personality}`")

        answer = "已保存个性化设置：\n\n" + "\n".join(lines)
        await self._mount_message("assistant", answer)
        self.session.add_message("assistant", answer)
        self._save_state()
        record = self._log_turn(text, answer, "personalization", None)
        self.retrieval.upsert_turn(record)
        self._refresh_sidebars()
        self._refresh_status("已保存个性化设置")
        self._refresh_mascot()
        self.query_one("#composer", Input).focus()
        return True

    def _extract_personalization_updates(self, text: str) -> Dict[str, str]:
        updates: Dict[str, str] = {}
        for key, patterns in (
            ("assistant_name", ASSISTANT_NAME_PATTERNS),
            ("user_name", USER_NAME_PATTERNS),
            ("personality", PERSONA_PATTERNS),
        ):
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    value = self._clean_personalization_value(match.group(1))
                    if value:
                        updates[key] = value
                        break
        return updates

    def _clean_personalization_value(self, value: str) -> str:
        cleaned = value.strip().strip("`'\"“”‘’")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"(?:吧|哈|哦|呀|谢谢|可以吗|行吗)$", "", cleaned).strip()
        if cleaned in {"什么", "啥", "谁", "哪位"}:
            return ""
        return cleaned[:80]

    async def _send_code_message(self, text: str, intent: Intent) -> None:
        assistant_bubble: Optional[MessageBubble] = None
        try:
            self._add_mentions_to_context(text)
            self.session.add_message("user", text)
            self._save_state()
            await self._mount_message("user", text)
            assistant_bubble = await self._mount_message("assistant", "代码代理正在生成编辑计划...")
            self._refresh_status("代码代理生成中")

            context_paths = self._select_code_context_paths(text)
            code_context = candidate_context(self.root, context_paths)
            messages = [
                {
                    "role": "system",
                    "content": self._code_edit_system_prompt(),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户需求：\n{text}\n\n"
                        f"当前相关文件上下文（敏感信息已遮蔽）：\n{code_context or '暂无文件上下文，请按需求创建或修改必要文件。'}"
                    ),
                },
            ]
            plan = await self.client.complete_json(
                model=self.session.model,
                messages=messages,
                temperature=0.2,
                thinking=self.session.thinking,
            )
            preview = preview_edit_plan(self.root, plan, context_paths)
            self.pending_edit = preview
            self.pending_user_input = text
            self.pending_intent = intent.kind
            remembered_rule = self.permissions.matching_rule(preview)
            if remembered_rule:
                assistant_bubble.set_body(
                    f"命中已记住的相似任务授权 `{remembered_rule.rule_id}`，正在应用变更..."
                )
                await self._apply_pending_edit(auto_rule=remembered_rule.rule_id, bubble=assistant_bubble)
                return
            answer = self._format_preview(preview)
            assistant_bubble.set_body(answer)
            self.session.add_message("assistant", answer)
            self._save_state()
            self._refresh_sidebars()
            self._refresh_status("等待审批")
            self._refresh_mascot()
        except (GLMAPIError, EditError, OSError) as exc:
            if assistant_bubble:
                assistant_bubble.set_body("代码代理失败：\n\n" + redact_secrets(str(exc)))
            self._refresh_status("代码代理失败")
        finally:
            self.current_task = None
            self.query_one("#composer", Input).focus()

    def _add_mentions_to_context(self, text: str) -> None:
        for mention in self.project.find_mentions(text):
            if mention in self.context_blocks:
                continue
            try:
                item = self.project.read_file(mention)
            except (OSError, ValueError):
                continue
            self.context_blocks[item.path] = item
            if item.path not in self.session.context_files:
                self.session.context_files.append(item.path)

    def _select_code_context_paths(self, text: str) -> List[str]:
        paths: List[str] = []
        for item in self.project.find_mentions(text):
            paths.append(item)
        paths.extend(self.context_blocks.keys())
        lowered = text.lower()
        candidates = []
        for path in self.project._iter_text_files():
            rel = path.relative_to(self.root).as_posix()
            score = 0
            if rel in paths:
                score += 100
            if path.name.lower() in lowered or rel.lower() in lowered:
                score += 50
            if path.suffix == ".py":
                score += 10
            if path.name in {"test.py", "run.sh"}:
                score += 8
            if "tui" in lowered and "app.py" in rel:
                score += 20
            candidates.append((score, rel))
        for _, rel in sorted(candidates, key=lambda item: (-item[0], item[1])):
            if rel not in paths:
                paths.append(rel)
            if len(paths) >= 12:
                break
        return paths

    def _code_edit_system_prompt(self) -> str:
        return (
            "你是一个自动代码修改代理。必须只返回 JSON object，不要 Markdown，不要解释。\n"
            "允许的编辑类型：write_file, create_file, replace_block, copy_file, delete_file。\n"
            "JSON schema：{"
            '"summary":"简短中文摘要",'
            '"edits":[{"type":"replace_block","path":"相对路径","search":"原文","replace":"新文"}]}。\n'
            "copy_file 格式：{\"type\":\"copy_file\",\"source\":\"源相对路径\",\"path\":\"目标相对路径\"}。\n"
            "replace_block 的 search 必须是文件中唯一存在的精确文本。"
            "write_file/create_file 必须提供完整 content。"
            "只能修改当前项目内文本文件，不要请求执行 shell。"
            "不要生成 rm -rf、shutil.rmtree、os.remove、unlink 等危险删除代码，除非用户明确要求。"
            "不要输出密钥、Bearer token 或 ZHIPUAI_API_KEY 的真实值。"
        )

    def _format_change(self, change) -> str:
        files = "\n".join(f"- `{path}`" for path in change.files) or "- 无文件变化"
        diff_preview = change.diff[:4000]
        if len(change.diff) > len(diff_preview):
            diff_preview += "\n...（diff 已截断）"
        return (
            f"## 已应用变更 `{change.change_id}`\n\n"
            f"{change.summary}\n\n"
            f"文件：\n{files}\n\n"
            f"统计：+{change.added_lines} / -{change.removed_lines}\n\n"
            f"```diff\n{diff_preview}\n```\n\n"
            "可使用 `/undo` 回滚最近一次 AI 变更。"
        )

    def _format_preview(self, preview: EditPreview) -> str:
        files = "\n".join(f"- `{path}`" for path in preview.files) or "- 无文件变化"
        reasons = "\n".join(f"- {item}" for item in preview.risk_reasons) or "- 低风险：仅限本轮上下文内文本替换/创建"
        warning = ""
        if preview.risk_level == "critical":
            warning = (
                "\n\n**危险警示：检测到 rm/递归删除/文件删除相关代码或操作。"
                "这类变更永远不会被记住为默认授权，只能本次手动 `/approve`。**\n"
            )
        diff_preview = preview.diff[:5000]
        if len(preview.diff) > len(diff_preview):
            diff_preview += "\n...（diff 已截断）"
        return (
            f"## 待审批代码变更\n\n"
            f"{preview.summary}\n\n"
            f"风险等级：`{preview.risk_level}`\n\n"
            f"{warning}"
            f"操作类型：`{', '.join(preview.operations)}`\n\n"
            f"风险原因：\n{reasons}\n\n"
            f"文件：\n{files}\n\n"
            f"统计：+{preview.added_lines} / -{preview.removed_lines}\n\n"
            f"```diff\n{diff_preview}\n```\n\n"
            "输入 `/approve` 应用，或 `/reject` 丢弃。低/中风险写入或复制任务可用 `/approve remember` 记住相似任务。审批前不会写入任何项目文件。"
        )

    async def _apply_pending_edit(
        self,
        remember: bool = False,
        auto_rule: Optional[str] = None,
        bubble: Optional[MessageBubble] = None,
    ) -> None:
        preview = self.pending_edit
        if preview is None:
            await self._notice("当前没有待审批代码变更。")
            return
        remembered_rule_id = None
        try:
            change = apply_edit_plan(self.root, preview.plan, self.checkpoints)
            if remember:
                rule = self.permissions.remember(preview)
                remembered_rule_id = rule.rule_id
        except ValueError as exc:
            await self._notice("已应用变更，但无法记住相似任务授权：\n\n" + str(exc))
            remembered_rule_id = None
        except (EditError, OSError) as exc:
            await self._notice("应用待审批变更失败：\n\n" + redact_secrets(str(exc)))
            self._refresh_status("审批应用失败")
            return
        self.pending_edit = None
        self.session.last_change_id = change.change_id
        answer = self._format_change(change)
        notes = []
        if remembered_rule_id:
            notes.append(f"已记住相似任务授权：`{remembered_rule_id}`。")
        if auto_rule:
            notes.append(f"本次基于已记住授权 `{auto_rule}` 自动应用。")
        if notes:
            answer += "\n\n" + "\n".join(notes)
        self.session.add_message("assistant", answer)
        self._save_state()
        record = self._log_turn(self.pending_user_input, answer, self.pending_intent, change.change_id)
        self.retrieval.upsert_turn(record)
        self._schedule_memory_sync(show_empty=False)
        self.pending_user_input = ""
        self._refresh_sidebars()
        self._refresh_status("已应用审批变更")
        self._refresh_mascot()
        if bubble:
            bubble.set_body(answer)
        else:
            await self._mount_message("assistant", answer)

    def _log_turn(self, user_input: str, assistant_output: str, intent: str, change_id: Optional[str]) -> TurnRecord:
        return self.logs.append(
            session_id=self.session.id,
            user_input=user_input,
            assistant_output=assistant_output,
            model=self.session.model,
            context_files=self.session.context_files,
            change_id=change_id,
            intent=intent,
        )

    async def _sync_memory_background(self, show_empty: bool) -> None:
        try:
            changes = await self.memory_manager.sync_with_llm(
                client=self.client,
                model=self.session.model,
                logs=self.logs,
                retrieval=self.retrieval,
            )
        except Exception as exc:
            if show_empty:
                await self._notice("记忆同步失败：\n\n" + redact_secrets(str(exc)))
            return
        self._refresh_sidebars()
        if changes:
            await self._notice("## 记忆更新\n\n" + "\n".join(f"- {item}" for item in changes))
        elif show_empty:
            await self._notice("没有未总结的新对话。")

    async def _generate_recap(self) -> None:
        bubble: Optional[MessageBubble] = None
        try:
            bubble = await self._mount_message("assistant", "正在生成会话摘要...")
            history = "\n\n".join(
                f"{message.role}: {message.content}" for message in self.session.messages[-12:]
            )
            messages = [
                {
                    "role": "system",
                    "content": "你是中文会议纪要助手。请用 3-5 条短句总结对话，便于下次恢复上下文。",
                },
                {"role": "user", "content": history},
            ]
            event = await self.client.complete_once(
                model=self.session.model,
                messages=messages,
                temperature=0.3,
                thinking=None,
            )
            recap = event.content.strip()
            self.memory.add_recap(recap)
            bubble.set_body("## 会话摘要\n\n" + recap + "\n\n已保存到长期记忆。")
            self._refresh_sidebars()
        except GLMAPIError as exc:
            if bubble:
                bubble.set_body("生成摘要失败：\n\n" + redact_secrets(str(exc)))
        finally:
            self.current_task = None

    def _rag_context_for(self, text: str, intent: Intent) -> str:
        if not self.session.rag_enabled:
            return ""
        detailed = intent.kind == "memory_detail"
        if intent.kind in {"memory_summary", "memory_detail"}:
            blocks = [self.memory_manager.context_for_query(text, self.retrieval, detailed=detailed)]
            recent_history = self._recent_history_context(limit=8 if detailed else 5)
            if recent_history:
                blocks.append(recent_history)
            return "\n\n".join(block for block in blocks if block)
        hits = self.retrieval.search_segments(text, limit=3)
        if not hits:
            return ""
        return "## 自动检索到的相关记忆\n" + "\n".join(
            f"[mem:{hit.item_id}] {hit.content[:600]}" for hit in hits
        )

    def _recent_history_context(self, limit: int = 6) -> str:
        records = self.logs.read_all(limit=limit)
        if not records:
            return ""
        blocks = []
        for record in records:
            blocks.append(
                f"[log:{record.session_id}:{record.turn_id}]\n"
                f"时间：{record.created_at}\n"
                f"用户：{record.user_input}\n"
                f"助手：{record.assistant_output[:900]}"
            )
        return (
            "## 最近原始对话历史\n"
            "用户询问上一轮、刚才、问过什么、聊天记录或历史记录时，优先依据这些原始日志回答。\n\n"
            + "\n\n".join(blocks)
        )

    def _system_prompt(self, rag_context: str = "") -> str:
        assistant_name = self.memory.get_profile("assistant_name", "GLM")
        user_name = self.memory.get_profile("user_name", "")
        personality = self.memory.get_profile("personality", "")
        parts = [
            f"你是一个运行在中文 TUI 里的 GLM 编程助手。你的当前名字是：{assistant_name}。",
            "你需要用中文回答，风格直接、清晰、务实。",
            "当前能力边界：普通聊天可以阅读上下文；代码代理模式会单独应用结构化编辑。",
            (
                "当用户问“你能干什么”“有什么功能”“会什么”“怎么用”等能力介绍类问题时，"
                "请直接用大模型自然回答你在这个 TUI 中能做的事：中文问答、代码解释和方案分析、"
                "读取用户加入的项目文件上下文、搜索/查看本地对话日志、使用长期记忆、生成会话摘要、"
                "在代码代理模式下生成待审批的代码修改方案，并提醒可输入 `/help` 查看全部命令。"
            ),
        ]
        if user_name:
            parts.append(f"当前用户名字是：{user_name}。合适时可用这个名字称呼用户。")
        if personality:
            parts.append(f"用户设置的回答风格：{personality}。在不损害准确性和安全性的前提下遵守。")
        memory_summary = self.memory.summary()
        if memory_summary:
            parts.append("长期记忆：\n" + memory_summary)
        auto_summary = self.memory_manager.summary_text()
        if auto_summary:
            parts.append("自动滚动记忆总结：\n" + auto_summary)
        if rag_context:
            parts.append("本轮自动 RAG 上下文：\n" + rag_context)
        if self.context_blocks:
            context = "\n\n".join(item.as_prompt_block() for item in self.context_blocks.values())
            parts.append("本轮只读项目上下文：\n" + context)
        return "\n\n".join(parts)

    def _extract_tasks(self, text: str) -> None:
        for line in text.splitlines():
            match = TASK_RE.match(line)
            if not match:
                continue
            mark = "x" if match.group(1).lower() == "x" else " "
            task = f"- [{mark}] {match.group(2).strip()}"
            if task not in self.tasks:
                self.tasks.append(task)

    def _extract_tasks_from_messages(self) -> None:
        self.tasks.clear()
        for message in self.session.messages:
            self._extract_tasks(message.content)

    async def _clear_chat_view(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        for child in list(chat.children):
            await child.remove()

    def _refresh_sidebars(self) -> None:
        left = self.query_one("#left-panel", Static)
        right = self.query_one("#right-panel", Static)

        context_lines = (
            "\n".join(f"- `{path}`" for path in self.context_blocks)
            if self.context_blocks
            else "暂无。使用 `/add test.py` 或 `@test.py` 加入。"
        )
        memory_summary = self.memory.summary() or "暂无长期记忆。"
        auto_summary = self.memory_manager.summary_text()
        if auto_summary:
            memory_summary += "\n\n自动总结：\n" + auto_summary[:500]
        search_lines = "暂无搜索。"
        if self.search_hits:
            search_lines = "\n".join(
                f"- `{hit.path}:{hit.line_no}` {hit.line[:80]}" for hit in self.search_hits[:8]
            )

        left.update(
            Markdown(
                "\n".join(
                    [
                        "## 工作区",
                        f"`{self.root}`",
                        "",
                        "## 只读上下文",
                        context_lines,
                        "",
                        "## 记忆摘要",
                        memory_summary,
                        "",
                        "## 搜索结果",
                        search_lines,
                    ]
                )
            )
        )

        usage = self.session.usage or {}
        usage_lines = "\n".join(f"- `{key}`: {value}" for key, value in usage.items()) or "暂无用量数据。"
        tasks = "\n".join(self.tasks[:12]) or "暂无任务。"
        thinking = "auto" if self.session.thinking is None else str(self.session.thinking).lower()
        pending_lines = "无待审批变更。"
        if self.pending_edit:
            pending_lines = "\n".join(
                [
                    f"- 摘要：{self.pending_edit.summary}",
                    f"- 风险：`{self.pending_edit.risk_level}`",
                    f"- 文件数：`{len(self.pending_edit.files)}`",
                    f"- 操作：`/approve` 或 `/reject`",
                ]
            )
        detail = ""
        if self.verbose:
            detail = "\n\n## 详细模式\n\n" + "\n".join(
                [
                    f"- 会话 ID：`{self.session.id}`",
                    f"- 消息数：`{len(self.session.messages)}`",
                    f"- 上下文文件：`{len(self.context_blocks)}`",
                    f"- 主题：`{self.theme_name}`",
                ]
            )
        right.update(
            Markdown(
                "\n".join(
                    [
                        "## 模型参数",
                        f"- 模型：`{self.session.model}`",
                        f"- 模式：`{self.session.mode}`",
                        f"- 审批：`{self.session.approval_policy}`",
                        f"- RAG：`{'on' if self.session.rag_enabled else 'off'}`",
                        f"- 温度：`{self.session.temperature}`",
                        f"- 流式：`{'on' if self.session.stream else 'off'}`",
                        f"- 思考：`{thinking}`",
                        f"- 最近变更：`{self.session.last_change_id or '-'}`",
                        "",
                        "## 待审批",
                        pending_lines,
                        "",
                        "## 任务清单",
                        tasks,
                        "",
                        "## 用量统计",
                        usage_lines,
                        detail,
                    ]
                )
            )
        )

    def _refresh_status(self, status: str) -> None:
        self.activity_status = status
        line = self.query_one("#status-line", Static)
        key_state = "已设置" if os.getenv("ZHIPUAI_API_KEY") else "缺失"
        line.update(
            f"状态：{status} | 模型：{self.session.model} | 模式：{self.session.mode} | 审批：{self.session.approval_policy} | 会话：{self.session.name} "
            f"| API Key：{key_state} | `/help` 查看命令"
        )
        self._refresh_activity()
        self._refresh_mascot()

    def _tick_activity(self) -> None:
        self.activity_index = (self.activity_index + 1) % len(self.activity_frames)
        self._refresh_activity()
        self._refresh_mascot()

    def _refresh_activity(self) -> None:
        try:
            line = self.query_one("#activity-line", Static)
        except NoMatches:
            return
        frame = self.activity_frames[self.activity_index]
        pending = "待审批: 有" if self.pending_edit else "待审批: 无"
        task = "生成中" if self.current_task and not self.current_task.done() else "空闲"
        guard = "写入保护: /approve 后才写文件"
        line.update(
            f"{frame} {self.activity_status}  |  {task}  |  {pending}  |  {guard}  |  {self.loading_phrases[self.activity_index % len(self.loading_phrases)]}"
        )

    def _refresh_mascot(self) -> None:
        try:
            mascot = self.query_one("#mascot-line", Static)
        except NoMatches:
            return
        face = self.mascot_frames[self.activity_index % len(self.mascot_frames)]
        sparkle = self.activity_frames[self.activity_index % len(self.activity_frames)]
        if self.pending_edit:
            mood = "发现一份待审批改动"
            hint = "输入 /approve 应用，或 /reject 丢弃"
            color = "#ffd166"
        elif self.current_task and not self.current_task.done():
            mood = "正在处理请求"
            hint = "Ctrl+C 可取消当前生成"
            color = "#8cffc1"
        else:
            mood = "空闲守候"
            hint = "输入 /help 查看命令，@文件 可加入上下文"
            color = "#bfffe0"
        text = Text()
        text.append(f"{sparkle} 糯米助手 {face}  ", style=f"bold {color}")
        text.append(f"{mood}", style="bold")
        text.append("  |  ")
        text.append(hint, style="#9ad9c0")
        text.append("  |  ")
        text.append(f"模型 {self.session.model} · 模式 {self.session.mode}", style="#8ecbff")
        mascot.update(
            Panel(
                Align.center(text),
                box=box.ROUNDED,
                border_style=color,
                padding=(0, 1),
            )
        )

    def _apply_theme(self, name: str) -> None:
        self.theme_name = name
        screen = self.screen
        for cls in ("theme-paper", "theme-neon"):
            screen.remove_class(cls)
        if name == "纸墨":
            screen.add_class("theme-paper")
        elif name == "霓虹":
            screen.add_class("theme-neon")
        self._refresh_sidebars()
        self._refresh_status("主题已切换")

    def action_cancel_generation(self) -> None:
        self._save_state()
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            self._refresh_status("正在取消")
        else:
            self.query_one("#composer", Input).focus()

    async def action_clear_screen(self) -> None:
        await self._clear_chat_view()
        await self._notice("屏幕已清空；会话消息仍保存在当前 session 中。")

    def action_toggle_tasks(self) -> None:
        panel = self.query_one("#right", Vertical)
        panel.toggle_class("hidden")
        self._refresh_status("任务面板已切换")

    def action_toggle_detail(self) -> None:
        self.verbose = not self.verbose
        self._refresh_sidebars()
        self._refresh_status("详细模式已切换")

    def action_command_prev(self) -> None:
        if not self._move_command_selection(-1):
            self.query_one("#composer", Input).focus()

    def action_command_next(self) -> None:
        if not self._move_command_selection(1):
            self.query_one("#composer", Input).focus()

    def action_complete_command(self) -> None:
        if not self._complete_selected_command():
            self.query_one("#composer", Input).focus()

    def action_focus_input(self) -> None:
        self.query_one("#composer", Input).focus()


def run() -> None:
    app = GLMTuiApp()
    try:
        app.run()
    finally:
        app._save_state()
