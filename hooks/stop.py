#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Stop hook executor that reads settings.json and executes matching hooks.

Usage:
    echo '{"session_id": "xxx", "transcript_path": "..."}' | python stop.py
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass


@dataclass
class HookCommand:
    type: str
    command: str
    asyncable: bool = True


STOP_HOOK_CONFIG: list[HookCommand] = [
    HookCommand(
        type="command",
        command="uv run ~/.claude/hooks/stop/check_todos_completed.py",
        asyncable=True,
    )
]


async def execute_hook_async(command: str, stdin_data: str, cwd: str) -> tuple[int, str, str]:
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(stdin_data.encode()), timeout=30.0)

        return (
            process.returncode or 0,
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
        )
    except TimeoutError:
        return 1, "", "Hook execution timed out"
    except Exception as e:
        return 1, "", f"Hook execution failed: {e}"


async def _main() -> int:
    if not STOP_HOOK_CONFIG:
        return 0

    stdin_data = sys.stdin.read()

    current_cwd = os.getcwd()
    if stdin_data:
        try:
            input_json = json.loads(stdin_data)
            current_cwd = input_json.get("cwd", os.getcwd())
        except json.JSONDecodeError:
            pass

    tasks = [execute_hook_async(hook.command, stdin_data, current_cwd) for hook in STOP_HOOK_CONFIG]
    results: list[tuple[int, str, str] | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)

    should_block = False
    all_stdout: list[str] = []
    all_stderr: list[str] = []

    for hook, result in zip(STOP_HOOK_CONFIG, results, strict=False):
        if isinstance(result, BaseException):
            print(f"Error executing hook: {hook.command}. {result=}", file=sys.stderr)
            continue

        returncode, stdout, stderr = result

        if stdout:
            all_stdout.append(stdout)

        if returncode == 2 and stderr:
            should_block = True
            all_stderr.append(stderr)

    if all_stdout:
        print("".join(all_stdout), file=sys.stdout, end="")

    if all_stderr:
        print("".join(all_stderr), file=sys.stderr, end="")

    return 2 if should_block else 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
