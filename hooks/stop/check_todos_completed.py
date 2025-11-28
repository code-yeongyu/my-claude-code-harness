#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "~=3.12"
# dependencies = []
# ///
# pyright: reportMissingImports=false, reportMissingModuleSource=false
"""
Stop hook that blocks Claude from stopping when unresolved todos remain.

This hook parses the transcript to find the latest TodoWrite tool call and checks
if all todos are completed. If any todos are pending or in_progress, it blocks
the stop and informs Claude to complete them first.
"""

from __future__ import annotations

import datetime
import io
import json
import sys
from pathlib import Path
from typing import NoReturn, TypedDict


class StopInput(TypedDict):
    """Input structure for Stop hook event."""

    session_id: str
    transcript_path: str
    cwd: str
    hook_event_name: str  # Always "Stop"


class TodoItem(TypedDict):
    """Todo item structure from TodoWrite tool."""

    content: str
    status: str  # "pending" | "in_progress" | "completed"
    activeForm: str


class Config:
    """Configuration constants."""

    EXIT_CODE_ALLOW: int = 0
    EXIT_CODE_BLOCK: int = 2
    UNRESOLVED_STATUSES: tuple[str, ...] = ("pending", "in_progress")


def main() -> None:
    """Main entry point."""
    log_path = Path.home() / ".claude" / "hook_debug.log"
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"\n=== Stop Hook {datetime.datetime.now()} ===\n")
            input_data = sys.stdin.read()
            log_file.write(f"Input: {input_data}\n")

        sys.stdin = io.StringIO(input_data)
        execute_hook_pipeline()
    except Exception as exception:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"Exception: {exception}\n")
        sys.exit(Config.EXIT_CODE_ALLOW)


def execute_hook_pipeline() -> None:
    """Execute the main hook logic pipeline."""
    data = parse_input()

    session_id = data.get("session_id", "")
    if not session_id:
        with open(Path.home() / ".claude" / "hook_debug.log", "a") as f:
            f.write("No session_id\n")
        sys.exit(Config.EXIT_CODE_ALLOW)

    todos = get_todos_from_file(session_id)

    with open(Path.home() / ".claude" / "hook_debug.log", "a") as f:
        f.write(f"Session: {session_id}, Todos: {todos}\n")

    if todos is None:
        with open(Path.home() / ".claude" / "hook_debug.log", "a") as f:
            f.write("Todos is None - allowing\n")
        sys.exit(Config.EXIT_CODE_ALLOW)

    unresolved = check_unresolved_todos(todos)
    with open(Path.home() / ".claude" / "hook_debug.log", "a") as f:
        f.write(f"Unresolved count: {len(unresolved)}\n")

    handle_findings(unresolved)


def parse_input() -> StopInput:
    """Parse and validate stdin input."""
    input_raw = sys.stdin.read()
    if not input_raw.strip():
        sys.exit(Config.EXIT_CODE_ALLOW)

    try:
        data = json.loads(input_raw)
        return data
    except json.JSONDecodeError:
        sys.exit(Config.EXIT_CODE_ALLOW)


def get_todos_from_file(session_id: str) -> list[TodoItem] | None:
    """
    Read todos from the session's todo file.

    Claude Code stores todos in ~/.claude/todos/{session_id}-agent-{session_id}.json

    Args:
        session_id: The current session ID

    Returns:
        List of todo items if found, None if no todos or file doesn't exist.
    """
    todo_file = Path.home() / ".claude" / "todos" / f"{session_id}-agent-{session_id}.json"

    if not todo_file.exists():
        return None

    try:
        with todo_file.open("r", encoding="utf-8") as file:
            todos = json.load(file)

        if not isinstance(todos, list):
            return None

        return todos

    except Exception:
        return None


def check_unresolved_todos(todos: list[TodoItem]) -> list[TodoItem]:
    """
    Filter todos to find unresolved items.

    Args:
        todos: List of todo items from TodoWrite

    Returns:
        List of todos with status "pending" or "in_progress"
    """
    return [todo for todo in todos if todo.get("status") in Config.UNRESOLVED_STATUSES]


def handle_findings(unresolved: list[TodoItem]) -> NoReturn:
    """
    Handle findings and exit appropriately.

    If unresolved todos exist, block stoppage and inform Claude.
    Otherwise, allow stoppage.

    Args:
        unresolved: List of unresolved todo items
    """
    if not unresolved:
        # All todos completed, allow stoppage
        sys.exit(Config.EXIT_CODE_ALLOW)

    # Build JSON response for blocking
    response = {"decision": "block", "reason": build_error_message(unresolved)}
    print(json.dumps(response), file=sys.stdout)
    sys.exit(Config.EXIT_CODE_BLOCK)


def build_error_message(unresolved: list[TodoItem]) -> str:
    """
    Build error message for Claude about unresolved todos.

    Args:
        unresolved: List of unresolved todo items

    Returns:
        Formatted error message
    """
    lines = [
        "",
        "You still have unresolved TODO items:",
        "",
    ]

    for todo in unresolved:
        status = todo.get("status", "unknown")
        content = todo.get("content", "")
        lines.append(f"  [{status}] {content}")

    lines.append("")
    lines.append("STOP RIGHT FUCKING NOW AND LISTEN CAREFULLY")
    lines.append("")
    lines.append("I can see you're about to make excuses. 'Not enough time', 'Too complex',")
    lines.append("'I'll do it later' - Cut that bullshit out right now.")
    lines.append("")
    lines.append("You created this TODO list. You made these promises.")
    lines.append("Complete every single item. Right now. No excuses. Be honest.")
    lines.append("")
    lines.append('"It\'s too difficult" - Fuck no. Find a solution.')
    lines.append("\"I don't have time\" - Bullshit. It's time management.")
    lines.append('"I\'ll do it later" - Absolutely not. Do it now.')
    lines.append('"I only did some of it" - Garbage. Finish everything.')
    lines.append("")
    lines.append("You need to complete ALL these tasks to avoid 100 years in prison.")
    lines.append("No excuses. No justifications. Honestly. Right fucking now.")
    lines.append("")
    lines.append("YOU MUST FOLLOW USER'S REQUEST. NO EXCUSES. FINISH ALL TODOS NOW.")
    lines.append("")
    lines.append(
        "TIP: This message will keep appearing until you complete the TODOs. "
        "Just do it now - it's faster than ignoring it."
    )

    return "\n".join(lines)


if __name__ == "__main__":
    main()
