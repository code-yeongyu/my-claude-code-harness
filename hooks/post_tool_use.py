#!/usr/bin/env python3
"""
PostToolUse hook executor that reads settings.json and executes matching hooks.

Usage:
    echo '{"tool_name": "Write", "tool_input": {...}}' | python post_tool_use.py
    python post_tool_use.py --tool Write < input.json
"""

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass

TODOLIST_CHECK_CMD = (
    "input=$(cat); "
    "file_path=$(printf '%s' \"$input\" | jq -r '.tool_input.file_path // .tool_input.target_file // \"\"'); "
    'if [[ "$file_path" =~ ai-todolist\\.md$ ]] && [[ -f "ai-todolist.md" ]]; then '
    "printf '%s' \"$input\" | ~/.claude/hooks/post-tool-use/remind-ai-todolist.sh; fi"
)


@dataclass
class HookCommand:
    type: str
    command: str
    asyncable: bool = False


@dataclass
class HookMatcher:
    matcher: str
    hooks: list[HookCommand]


POST_TOOL_USE_CONFIG: list[HookMatcher] = [
    HookMatcher(
        matcher="Write|Edit|MultiEdit",
        hooks=[
            HookCommand(
                type="command",
                command="~/.claude/hooks/post-tool-use/system-reminder.sh",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_conftest.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_knowledge.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_language_guide.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/typescript_typecheck.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_auto_fix_init_reexport.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_lint_and_format.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_type_checker.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_check_any_return.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/check_typeddict_total_false.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_check_comments.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_check_nested_imports.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/python_check_match_case.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/check_corrupted_encoding.py",
                asyncable=True,
            ),
            HookCommand(
                type="command",
                command=TODOLIST_CHECK_CMD,
                asyncable=False,
            ),
        ],
    ),
    HookMatcher(
        matcher="Read",
        hooks=[
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_knowledge.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_language_guide.py",
                asyncable=False,
            ),
            HookCommand(
                type="command",
                command="uv run ~/.claude/hooks/post-tool-use/inject_conftest.py",
                asyncable=False,
            ),
        ],
    ),
    HookMatcher(
        matcher="Task",
        hooks=[
            HookCommand(
                type="command",
                command="~/.claude/hooks/post-tool-use/remind-execution.sh",
                asyncable=False,
            )
        ],
    ),
]


def match_tool(tool_name: str, matcher: str) -> bool:
    pattern = f"^({matcher})$"
    return bool(re.match(pattern, tool_name))


def is_self_hook(command: str) -> bool:
    return "post_tool_use.py" in command


async def execute_hook_async(command: str, stdin_data: str, cwd: str) -> tuple[int, str, str]:
    try:
        env = os.environ.copy()
        env["POST_TOOL_USE_RUNNING"] = "1"
        env["CLAUDE_CODE_CWD"] = cwd

        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
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
    if os.environ.get("POST_TOOL_USE_RUNNING"):
        return 0

    tool_name: str | None = None
    if len(sys.argv) > 1 and sys.argv[1] == "--tool" and len(sys.argv) > 2:
        tool_name = sys.argv[2]

    stdin_data = sys.stdin.read()

    # Extract actual Claude Code cwd from stdin JSON
    current_cwd = os.getcwd()  # fallback
    if stdin_data:
        try:
            input_json = json.loads(stdin_data)
            current_cwd = input_json.get("cwd", os.getcwd())
            if not tool_name:
                tool_name = input_json.get("tool_name")
        except json.JSONDecodeError:
            pass

    if not tool_name:
        print("Error: tool_name not provided", file=sys.stderr)
        return 1

    matching_hooks: list[HookCommand] = []
    for config in POST_TOOL_USE_CONFIG:
        if match_tool(tool_name, config.matcher):
            matching_hooks.extend(config.hooks)

    if not matching_hooks:
        print(f"No hooks found for tool: {tool_name}", file=sys.stderr)
        return 0

    valid_hooks = [
        hook for hook in matching_hooks if hook.type == "command" and hook.command and not is_self_hook(hook.command)
    ]

    if not valid_hooks:
        return 0

    async_hooks = [hook for hook in valid_hooks if hook.asyncable]
    sync_hooks = [hook for hook in valid_hooks if not hook.asyncable]
    claude_needs_to_know = False

    if async_hooks:
        tasks = [execute_hook_async(hook.command, stdin_data, current_cwd) for hook in async_hooks]
        results: list[tuple[int, str, str] | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)

        for hook, result in zip(async_hooks, results, strict=False):
            if isinstance(result, BaseException):
                continue

            returncode, _stdout, stderr = result
            if returncode == 2 and stderr:
                claude_needs_to_know = True
                print(stderr, file=sys.stderr, end="")

    for hook in sync_hooks:
        returncode, _stdout, stderr = await execute_hook_async(hook.command, stdin_data, current_cwd)

        if returncode == 2 and stderr:
            claude_needs_to_know = True
            print(f"Hook Executed: {hook.command}", file=sys.stderr)
            print(stderr, file=sys.stderr, end="")

    exit_code = 0
    if claude_needs_to_know:
        exit_code = 2

    return exit_code


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
