#!/usr/bin/env python3
"""
UserPromptSubmit hook that generates system reminder context in pure Python.
This is a Python-only implementation without external shell script dependencies.
Uses asyncio for concurrent subprocess execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import NamedTuple


def main() -> None:
    """Entry point that runs the async main function."""
    asyncio.run(_main())


async def _main() -> None:
    """Async main function that handles the hook logic."""
    try:
        json.load(sys.stdin)

        system_info = await generate_system_reminder_async()

        print(system_info)
        sys.exit(0)

    except Exception as e:
        print(f"[Hook Error] UserPromptSubmit hook failed: {str(e)}", file=sys.stderr)
        sys.exit(1)


class CommandResult(NamedTuple):
    """Result from running a command."""

    stdout: str
    stderr: str
    returncode: int


async def run_command(cmd: list[str], command_timeout: float = 1.0) -> CommandResult:
    """Run a command asynchronously and return stdout, stderr, and returncode."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            async with asyncio.timeout(command_timeout):
                stdout, stderr = await proc.communicate()
            return CommandResult(
                stdout=stdout.decode("utf-8") if stdout else "",
                stderr=stderr.decode("utf-8") if stderr else "",
                returncode=proc.returncode or 0,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult("", "", -1)

    except Exception:
        return CommandResult("", "", -1)


async def get_git_info_async() -> dict[str, str | int | None]:
    """Get git repository information if available."""
    git_check = await run_command(["git", "rev-parse", "--git-dir"])
    if git_check.returncode != 0:
        return {}

    async with asyncio.TaskGroup() as tg:
        branch_task = tg.create_task(run_command(["git", "branch", "--show-current"]))
        commit_hash_task = tg.create_task(run_command(["git", "rev-parse", "--short", "HEAD"]))
        commit_message_task = tg.create_task(run_command(["git", "log", "-1", "--pretty=%s"]))
        commit_author_task = tg.create_task(run_command(["git", "log", "-1", "--pretty=%an"]))
        commit_time_task = tg.create_task(run_command(["git", "log", "-1", "--pretty=%ar"]))
        status_task = tg.create_task(run_command(["git", "status", "--porcelain"]))
        git_root_task = tg.create_task(run_command(["git", "rev-parse", "--show-toplevel"]))

    git_info = {}

    branch_stdout = branch_task.result().stdout.strip()
    git_info["branch"] = branch_stdout or "detached"

    commit_hash_stdout = commit_hash_task.result().stdout.strip()
    if commit_hash_stdout:
        git_info["commit_hash"] = commit_hash_stdout

    commit_message_stdout = commit_message_task.result().stdout.strip()
    if commit_message_stdout:
        git_info["commit_message"] = commit_message_stdout

    commit_author_stdout = commit_author_task.result().stdout.strip()
    if commit_author_stdout:
        git_info["commit_author"] = commit_author_stdout

    commit_time_stdout = commit_time_task.result().stdout.strip()
    if commit_time_stdout:
        git_info["commit_time"] = commit_time_stdout

    status_lines = status_task.result().stdout.splitlines()
    unstaged_count = len([line for line in status_lines if line.strip()])
    if unstaged_count > 0:
        git_info["unstaged"] = unstaged_count

    git_root_path = git_root_task.result().stdout.strip()
    if git_root_path and git_root_path != os.getcwd():
        git_info["root"] = git_root_path

    return git_info


async def get_python_info_async() -> tuple[str | None, str | None]:
    """Get Python version and environment information."""
    result = await run_command(["python", "--version"])

    if result.returncode != 0:
        return None, None

    python_version = result.stdout.strip().replace("Python ", "")

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        venv_name = os.path.basename(virtual_env)
        return python_version, f"venv: {venv_name}"
    else:
        return python_version, "global"


def check_virtual_env() -> list[str]:
    """Check for virtual environment mismatches."""
    messages = []
    current_dir = os.getcwd()

    local_venv = None
    if os.path.isdir(os.path.join(current_dir, ".venv")):
        local_venv = os.path.join(current_dir, ".venv")
    elif os.path.isdir(os.path.join(current_dir, "venv")):
        local_venv = os.path.join(current_dir, "venv")

    if not local_venv:
        return messages

    virtual_env = os.environ.get("VIRTUAL_ENV")

    if virtual_env and local_venv:
        real_virtual_env = os.path.realpath(virtual_env)
        real_local_venv = os.path.realpath(local_venv)

        if real_virtual_env != real_local_venv:
            messages.extend(
                [
                    "[WARNING] Virtual environment mismatch detected!",
                    f"Current active venv: {virtual_env}",
                    f"Local project venv: {local_venv}",
                    "Consider using 'uv run' or 'poetry run' for project-specific commands.",
                    f"Or activate the correct venv: source {local_venv}/bin/activate",
                ]
            )
    elif local_venv and not virtual_env:
        messages.extend(
            [
                "[NOTICE] Local virtual environment found but not activated",
                f"Local project venv: {local_venv}",
                "Consider using 'uv run' or 'poetry run' for project-specific commands.",
                f"Or activate the venv: source {local_venv}/bin/activate",
            ]
        )

    return messages


async def generate_system_reminder_async() -> str:
    """Generate the full system reminder text asynchronously."""
    lines: list[str] = []

    current_time = datetime.now().astimezone().isoformat()

    lines.extend(
        [
            "",
            "[system-reminder]",
            f"CURRENT SYSTEM TIME: {current_time} (IT IS NOT 2024)",
            "User Language: 한국어 (Korean)",
            "**사용자에게 항상, 무조건 한국어로 답변하세요.**",
            "Claude Language: English - make sure you think in English",
        ]
    )

    async with asyncio.TaskGroup() as tg:
        git_task = tg.create_task(get_git_info_async())
        python_task = tg.create_task(get_python_info_async())

    git_info = git_task.result()
    if git_info:
        if "branch" in git_info:
            lines.append(f"Git branch: {git_info['branch']}")

        if all(key in git_info for key in ["commit_hash", "commit_message", "commit_author", "commit_time"]):
            lines.append(
                f'Git commit: [{git_info["commit_hash"]}] "{git_info["commit_message"]}" '
                f"by {git_info['commit_author']} ({git_info['commit_time']})"
            )

        if "unstaged" in git_info:
            lines.append(f"Git unstaged: {git_info['unstaged']} files")

        if "root" in git_info:
            lines.append(f"Git root: {git_info['root']}")

    python_version, env_type = python_task.result()
    if python_version:
        lines.append(f"Python: {python_version} ({env_type})")

    venv_messages = check_virtual_env()
    if venv_messages:
        lines.extend(venv_messages)

    lines.extend(
        [
            "",
            "[ABSOLUTE TASK COMPLETION RULE]",
            "NEVER STOP until the user's EXACT request is 100% complete.",
            "If ANY part of the user's task remains unfinished:",
            "- DO NOT say 'I cannot' or 'This is difficult'",
            "- DO NOT give up or suggest alternatives",
            "- CONTINUE working until EVERY requirement is met",
            "The task is NOT complete until the user explicitly confirms completion.",
            "Keep pushing. Keep trying. Complete the task fully.",
        ]
    )

    lines.extend(
        [
            "",
            f"CURRENT SYSTEM TIME: {current_time} (IT IS NOT 2024)",
            "User Language: 한국어 (Korean)",
            "**사용자에게 항상, 무조건 한국어로 답변하세요.**",
            "Claude Language: English - make sure you think in English",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    main()
