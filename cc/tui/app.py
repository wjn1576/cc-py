"""Textual TUI app for cc-py.

This frontend reuses the same QueryEngine/runtime wiring as the classic REPL.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Footer, Header, Input, Static

from cc.core.events import (
    CompactOccurred,
    ErrorEvent,
    TextDelta,
    ThinkingDelta,
    ToolResultReady,
    ToolUseStart,
    TurnComplete,
)
from cc.main import (
    _build_system,
    _create_client_for_model,
    _load_env,
    build_skill_generator_prompt,
    build_runtime,
    format_skills_list,
    poll_team_inbox,
    reload_runtime_skills,
    save_runtime_session,
)
from cc.models.messages import Message, UserMessage
from cc.session.history import HistoryEntry, add_to_history
from cc.tui.pet import PixelPetWidget
from cc.ui.renderer import ACCENT, APP_NAME


TRUST_FILE = Path.home() / ".cc-py" / "trusted_workspaces.json"


def _trusted_workspaces() -> set[str]:
    try:
        data = json.loads(TRUST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data if isinstance(item, str)}


def _is_workspace_trusted(cwd: str) -> bool:
    return str(Path(cwd).resolve()) in _trusted_workspaces()


def _trust_workspace(cwd: str) -> None:
    trusted = _trusted_workspaces()
    trusted.add(str(Path(cwd).resolve()))
    TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRUST_FILE.write_text(json.dumps(sorted(trusted), indent=2), encoding="utf-8")


class CCPyTuiApp(App[None]):
    """Full-screen Textual interface for cc-py."""

    CSS = """
    Screen {
        background: #0f0f0f;
        color: #e7e7e7;
    }

    #main {
        height: 1fr;
    }

    #log {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        scrollbar-color: #555555;
        scrollbar-background: #0f0f0f;
    }

    #sidebar {
        width: 30;
        height: 1fr;
        border-left: solid #333333;
        padding: 0;
    }

    #pet {
        height: auto;
        content-align: center middle;
        padding: 1 0 0 0;
    }

    #status {
        height: 1fr;
        padding: 1;
        color: #cfcfcf;
    }

    #command-palette {
        display: none;
        max-height: 10;
        padding: 0 1;
        background: #151515;
        border-top: solid #4a4a4a;
        color: #d8d8d8;
    }

    #command-palette.visible {
        display: block;
    }

    #prompt {
        border: none;
        border-top: solid #777777;
        border-bottom: solid #777777;
        background: #0f0f0f;
        height: 3;
        padding: 0 1;
    }

    #prompt:focus {
        border: none;
        border-top: solid #777777;
        border-bottom: solid #777777;
    }

    #prompt > .input--cursor {
        background: #e7e7e7;
        color: #0f0f0f;
    }

    .welcome {
        margin: 1 0 2 0;
        color: #ff7a45;
    }

    .trust {
        margin: 2 0;
    }

    .user-message {
        margin: 1 0 0 0;
        padding: 0 1;
        background: #333333;
        color: #ffffff;
    }

    .assistant-message {
        margin: 1 0 0 0;
        color: #f0f0f0;
    }

    .system-message {
        margin: 1 0 0 0;
        color: #9c9c9c;
    }

    .tool-message {
        margin: 1 0 0 0;
        color: #80dfff;
    }

    .activity-message {
        margin: 1 0 0 0;
        color: #ff7a45;
    }

    .error-message {
        margin: 1 0 0 0;
        color: #ff6b6b;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear"),
        ("f1", "show_help", "Help"),
        ("escape", "cancel_or_hide", "Cancel"),
    ]

    def __init__(self, *, model: str, cwd: str, resume_id: str | None = None) -> None:
        super().__init__()
        self.model = model
        self.cwd = cwd
        self.resume_id = resume_id
        self.runtime: Any | None = None
        self.status = "starting"
        self.last_tool = "-"
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._assistant_text = ""
        self._assistant_widget: Static | None = None
        self._awaiting_trust = False
        self._focus_timer: Any | None = None
        self._activity_timer: Any | None = None
        self._activity_widget: Static | None = None
        self._activity_started_at = 0.0
        self._activity_tick = 0
        self._activity_word_index = 0
        self._activity_words = [
            "Hashing",
            "Computing",
            "Concocting",
            "Crunching",
            "Baking",
            "Thinking",
        ]
        self._activity_spinners = ["*", "·", "+", "·"]
        self._activity_done_words = [
            "Crunched",
            "Baked",
            "Computed",
            "Concocted",
        ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Horizontal(id="main"):
                yield VerticalScroll(id="log")
                with Vertical(id="sidebar"):
                    yield PixelPetWidget(id="pet")
                    yield Static(id="status")
            yield Static(id="command-palette")
            yield Input(placeholder="Ask cc-py, or type /help", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = APP_NAME
        self.sub_title = "TUI"
        self._focus_timer = self.set_interval(0.25, self._keep_prompt_focused)
        self._awaiting_trust = not _is_workspace_trusted(self.cwd)
        if self._awaiting_trust:
            self.status = "waiting for trust"
            self._write_trust_prompt()
            self._update_status()
            prompt = self.query_one("#prompt", Input)
            prompt.placeholder = "Enter to trust this workspace, 2/Esc to exit"
            prompt.focus()
            return
        await self._start_runtime()

    async def _start_runtime(self) -> None:
        self.runtime = await build_runtime(
            model=self.model,
            cwd=self.cwd,
            resume_id=self.resume_id,
            is_interactive=True,
        )
        self.status = "idle"
        self._write_welcome()
        for notice in self.runtime.notices:
            self._write_dim(notice)
        self._update_status()
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = "Ask cc-py, or type /help"
        prompt.focus()

    def action_clear_log(self) -> None:
        self.query_one("#log", VerticalScroll).remove_children()
        self._assistant_widget = None
        self._assistant_text = ""
        self._write_dim("Log cleared.")

    def action_show_help(self) -> None:
        self._hide_command_palette()
        self._write_help()

    def action_cancel_or_hide(self) -> None:
        if self._awaiting_trust:
            self.exit()
            return
        self._hide_command_palette()
        self._focus_prompt()

    def on_click(self) -> None:
        self._focus_prompt()

    async def on_key(self, event: Key) -> None:
        prompt = self.query_one("#prompt", Input)
        if prompt.disabled or prompt.has_focus:
            return
        if event.character and not event.key.startswith("ctrl+"):
            prompt.focus()
            prompt.insert_text_at_cursor(event.character)
            self._update_command_palette(prompt.value)
            event.stop()
            return
        self._focus_prompt()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if self._awaiting_trust:
            return
        self._update_command_palette(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        event.input.value = ""

        if self._awaiting_trust:
            await self._handle_trust_input(user_input)
            return

        self._hide_command_palette()
        if not user_input or self.runtime is None:
            return
        self._process_input(user_input)

    async def _handle_trust_input(self, user_input: str) -> None:
        normalized = user_input.lower()
        if normalized in {"", "1", "y", "yes"}:
            _trust_workspace(self.cwd)
            self._awaiting_trust = False
            self.query_one("#log", VerticalScroll).remove_children()
            await self._start_runtime()
            return
        if normalized in {"2", "n", "no", "q", "quit", "exit"}:
            self.exit()
            return
        self._write_error("Choose 1/Enter to trust this workspace, or 2/Esc to exit.")

    @work(exclusive=True, group="agent")
    async def _process_input(self, user_input: str) -> None:
        if self.runtime is None:
            return

        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        self.status = "running"
        self._set_pet_state("thinking")
        self._update_status()
        try:
            if user_input.startswith("/"):
                should_run = await self._handle_slash_command(user_input)
                if should_run:
                    for notice in await poll_team_inbox(self.runtime.engine, self.runtime.messages):
                        self._write_dim(notice)
                    await self._run_agent_turn()
                    self._after_turn()
                return

            self._write_user(user_input)
            self.runtime.messages.append(UserMessage(content=user_input))
            add_to_history(HistoryEntry(
                display=user_input[:200],
                timestamp=time.time(),
                project=self.cwd,
                session_id=self.runtime.session_id,
            ))

            for notice in await poll_team_inbox(self.runtime.engine, self.runtime.messages):
                self._write_dim(notice)

            await self._run_agent_turn()
            self._after_turn()
        finally:
            self.status = "idle"
            self._set_pet_state("idle")
            self._update_status()
            prompt.disabled = False
            prompt.focus()

    async def _handle_slash_command(self, user_input: str) -> bool:
        from cc.commands.registry import get_command, parse_slash_command

        assert self.runtime is not None

        cmd_name, cmd_args = parse_slash_command(user_input)
        if not cmd_name:
            self._write_help()
            return False

        cmd = get_command(cmd_name)
        if cmd is None:
            self._write_error(f"Unknown command: /{cmd_name}")
            return False

        result = cmd.handler(
            args=cmd_args,
            current_model=self.runtime.engine.model,
            total_input_tokens=self.runtime.engine.total_input_tokens,
            total_output_tokens=self.runtime.engine.total_output_tokens,
        )

        if result == "__CLEAR__":
            self.runtime.messages.clear()
            self.query_one("#log", VerticalScroll).remove_children()
            self._assistant_widget = None
            self._assistant_text = ""
            self._write_dim("Conversation cleared.")
            return False

        if result == "__COMPACT__":
            from cc.compact.compact import compact_messages

            self.status = "compacting"
            self._update_status()
            compacted = await compact_messages(
                self.runtime.messages,
                self.runtime.engine.make_call_model(max_tokens=4096),
            )
            self.runtime.messages.clear()
            self.runtime.messages.extend(compacted)
            self._write_dim("Context compacted.")
            return False

        if isinstance(result, str) and result.startswith("__MODEL__"):
            new_model = result[len("__MODEL__"):]
            env = _load_env(self.cwd)
            new_client = _create_client_for_model(new_model, env)
            if new_client is None:
                self._write_error(f"Model switch failed: missing API key for {new_model}")
                return False
            self.runtime.engine._client = new_client  # type: ignore[assignment]
            self.runtime.engine.model = new_model
            self.runtime.engine.system_prompt = _build_system(
                self.cwd,
                self.runtime.engine.model,
                self.runtime.claude_md,
            )
            self.model = new_model
            self._write_dim(f"Model changed to: {new_model}")
            self._update_status()
            return False

        if isinstance(result, str) and result.startswith("__SKILL__"):
            from cc.skills.loader import get_skill_by_name

            skill_name = result[len("__SKILL__"):]
            found_skill = get_skill_by_name(self.runtime.skills, skill_name)
            if found_skill:
                self.runtime.messages.append(UserMessage(content=found_skill.prompt))
                self._write_dim(f"Skill /{skill_name} activated.")
                return True
            else:
                self._write_error(f"Skill not found: {skill_name}")
            return False

        if result == "__SKILLS__":
            self._write_system(format_skills_list(self.runtime.skills))
            return False

        if result == "__RELOAD_SKILLS__":
            self._write_system(reload_runtime_skills(self.runtime))
            return False

        if result == "__RUN_SKILL_GENERATOR__":
            self.runtime.messages.append(UserMessage(content=build_skill_generator_prompt(self.cwd)))
            self._write_dim("Skill generator prompt activated.")
            return True

        self._write_system(str(result))
        return False

    async def _run_agent_turn(self) -> None:
        assert self.runtime is not None

        self._start_activity()
        try:
            async for event in self.runtime.engine.run_turn():
                self._render_event(event)
                if isinstance(event, TurnComplete):
                    self.runtime.engine._total_input_tokens += event.usage.input_tokens
                    self.runtime.engine._total_output_tokens += event.usage.output_tokens
        finally:
            self._finish_assistant_message()
            if self._activity_started_at:
                self._finish_activity(final_word="Stopped")

    def _after_turn(self) -> None:
        assert self.runtime is not None
        save_runtime_session(
            self.runtime.engine,
            self.runtime.session_id,
            self.runtime.messages,
        )

        call_model = self.runtime.engine.make_call_model(max_tokens=1024)
        messages = self.runtime.messages
        cwd = self.cwd
        extraction_coord = self.runtime.extraction_coord

        async def _bg_extract() -> None:
            try:
                saved = await extraction_coord.request_extraction(messages, cwd, call_model)
                if saved:
                    self._write_dim(f"Saved {len(saved)} memory(s): {', '.join(saved)}")
            except Exception:
                return

        task = asyncio.create_task(_bg_extract())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _render_event(self, event: Any) -> None:
        if isinstance(event, TextDelta):
            self._clear_activity()
            self._set_pet_state("talking")
            self._append_assistant_text(event.text)
        elif isinstance(event, ThinkingDelta):
            self._clear_activity()
            self._set_pet_state("thinking")
            self._write_dim(event.text)
        elif isinstance(event, ToolUseStart):
            self._clear_activity()
            self._set_pet_state("working")
            self._finish_assistant_message()
            self.last_tool = event.tool_name
            self._update_status()
            preview = str(event.input)
            if len(preview) > 160:
                preview = preview[:160] + "..."
            self._write_tool(f"[{event.tool_name}] {preview}")
        elif isinstance(event, ToolResultReady):
            self._clear_activity()
            self._finish_assistant_message()
            preview = event.content[:300] if isinstance(event.content, str) else str(event.content)[:300]
            if event.is_error:
                self._set_pet_state("error")
                self._write_error(preview)
            else:
                self._set_pet_state("success")
                self._write_tool(preview, dim=True)
        elif isinstance(event, CompactOccurred):
            self._clear_activity()
            self._set_pet_state("compact")
            self._finish_assistant_message()
            self._write_system("Context compacted.")
        elif isinstance(event, TurnComplete):
            self._finish_assistant_message()
            if event.usage.input_tokens > 0 or event.usage.output_tokens > 0:
                self._write_dim(f"{event.usage.input_tokens} in / {event.usage.output_tokens} out tokens")
            self._finish_activity()
        elif isinstance(event, ErrorEvent):
            self._clear_activity()
            self._set_pet_state("error")
            self._finish_assistant_message()
            self._write_error(event.message)
            self._finish_activity(final_word="Stopped")

    def _append_assistant_text(self, chunk: str) -> None:
        if self._assistant_widget is None:
            self._assistant_text = ""
            self._assistant_widget = self._mount_log(Static(classes="assistant-message"))
        self._assistant_text += chunk
        text = Text("● ", style="bold white")
        text.append(self._assistant_text)
        self._assistant_widget.update(text)
        self._scroll_to_end()

    def _finish_assistant_message(self) -> None:
        self._assistant_widget = None
        self._assistant_text = ""

    def _start_activity(self) -> None:
        self._finish_activity(write_final=False)
        self._activity_started_at = time.monotonic()
        self._activity_tick = 0
        self._activity_word_index = 0
        self._activity_widget = self._mount_log(Static(classes="activity-message"))
        self._animate_activity()
        self._activity_timer = self.set_interval(0.55, self._animate_activity)

    def _animate_activity(self) -> None:
        if self._activity_widget is None:
            return

        if self._activity_tick and self._activity_tick % 6 == 0:
            self._activity_word_index = (self._activity_word_index + 1) % len(self._activity_words)

        word = self._activity_words[self._activity_word_index]
        marker = self._activity_spinners[self._activity_tick % len(self._activity_spinners)]
        dots = "." * ((self._activity_tick % 3) + 1)

        text = Text()
        text.append(marker, style=f"bold {ACCENT}")
        text.append(" ")
        text.append(word, style=f"bold {ACCENT}")
        text.append(dots, style="#ffb08a")
        self._activity_widget.update(text)
        self._activity_tick += 1
        self._scroll_to_end()

    def _clear_activity(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        if self._activity_widget is not None:
            self._activity_widget.remove()
            self._activity_widget = None

    def _finish_activity(self, *, final_word: str | None = None, write_final: bool = True) -> None:
        if not self._activity_started_at:
            self._clear_activity()
            return
        elapsed = max(0, int(time.monotonic() - self._activity_started_at))
        done_word = final_word or self._activity_done_words[self._activity_tick % len(self._activity_done_words)]
        self._clear_activity()
        self._activity_started_at = 0.0
        if write_final:
            self._write_dim(f"* {done_word} for {elapsed}s")

    def _write_trust_prompt(self) -> None:
        content = Text()
        content.append("Accessing workspace:\n\n", style="bold yellow")
        content.append(f"{self.cwd}\n\n", style="bold")
        content.append(
            "Quick safety check: Is this a project you created or one you trust? "
            "If not, review what's in this folder first.\n\n"
        )
        content.append("cc-py will be able to read, edit, and execute files here.\n\n")
        content.append("Security guide\n\n", style="dim")
        content.append("> 1. Yes, I trust this folder\n", style="bold #b7c7ff")
        content.append("  2. No, exit\n\n")
        content.append("Enter to confirm · Esc to cancel", style="dim")
        self._mount_log(Static(content, classes="trust"))

    def _write_welcome(self) -> None:
        left = Text()
        left.append(f"{APP_NAME}", style=f"bold {ACCENT}")
        left.append(" - TUI\n", style="dim")
        left.append(f"model: {self.model}\n", style="dim")
        left.append(f"cwd: {self.cwd}\n", style="dim")
        left.append("/help commands | Ctrl+Q quit | Ctrl+L clear", style="dim")
        self._mount_log(Static(left, classes="welcome"))

    def _write_help(self) -> None:
        from cc.commands.registry import list_commands

        lines = ["Available commands:"]
        for cmd in sorted(list_commands(), key=lambda c: c.name):
            lines.append(f"/{cmd.name} - {cmd.description}")
        self._write_system("\n".join(lines))

    def _write_user(self, text: str) -> None:
        self._mount_log(Static(f"> {text}", classes="user-message"))

    def _write_system(self, text: str) -> None:
        self._mount_log(Static(text, classes="system-message"))

    def _write_tool(self, text: str, *, dim: bool = False) -> None:
        render = Text(text, style="dim #80dfff" if dim else "bold #80dfff")
        self._mount_log(Static(render, classes="tool-message"))

    def _write_error(self, text: str) -> None:
        self._mount_log(Static(f"Error: {text}", classes="error-message"))

    def _write_dim(self, text: str) -> None:
        self._mount_log(Static(Text(text, style="dim"), classes="system-message"))

    def _mount_log(self, widget: Static) -> Static:
        log = self.query_one("#log", VerticalScroll)
        log.mount(widget)
        self._scroll_to_end()
        return widget

    def _scroll_to_end(self) -> None:
        log = self.query_one("#log", VerticalScroll)
        self.call_after_refresh(log.scroll_end, animate=False)

    def _focus_prompt(self) -> None:
        prompt = self.query_one("#prompt", Input)
        if not prompt.disabled:
            prompt.focus()

    def _keep_prompt_focused(self) -> None:
        self._focus_prompt()

    def _set_pet_state(self, state: str) -> None:
        try:
            self.query_one("#pet", PixelPetWidget).set_state(state)
        except Exception:
            return

    def _update_command_palette(self, raw_text: str) -> None:
        palette = self.query_one("#command-palette", Static)
        if not raw_text.startswith("/"):
            self._hide_command_palette()
            return

        from cc.commands.registry import list_commands

        command_part = raw_text[1:].split(None, 1)[0] if raw_text[1:].strip() else ""
        commands = sorted(list_commands(), key=lambda c: c.name)
        matches = [cmd for cmd in commands if cmd.name.startswith(command_part)]
        if not matches:
            palette.update(Text("No matching commands", style="dim"))
            palette.add_class("visible")
            return

        lines: list[Text] = []
        for cmd in matches[:10]:
            line = Text(f"/{cmd.name:<16}", style="bold #b7c7ff")
            line.append(cmd.description, style="dim")
            lines.append(line)
        if len(matches) > 10:
            more = Text(f"... {len(matches) - 10} more", style="dim")
            lines.append(more)

        body = Text()
        for index, line in enumerate(lines):
            if index:
                body.append("\n")
            body.append_text(line)
        palette.update(body)
        palette.add_class("visible")

    def _hide_command_palette(self) -> None:
        self.query_one("#command-palette", Static).remove_class("visible")

    def _update_status(self) -> None:
        if self.runtime is None:
            model = self.model
            session_id = "-"
            input_tokens = 0
            output_tokens = 0
        else:
            model = self.runtime.engine.model
            session_id = self.runtime.session_id
            input_tokens = self.runtime.engine.total_input_tokens
            output_tokens = self.runtime.engine.total_output_tokens

        status = (
            f"[b]{APP_NAME}[/b]\n\n"
            f"status: {self.status}\n"
            f"model: {model}\n"
            f"cwd: {self.cwd}\n"
            f"session: {session_id}\n\n"
            f"input tokens: {input_tokens}\n"
            f"output tokens: {output_tokens}\n"
            f"last tool: {self.last_tool}\n\n"
            "Keys:\n"
            "Ctrl+Q quit\n"
            "Ctrl+L clear\n"
            "F1 help"
        )
        self.query_one("#status", Static).update(status)
