#!/usr/bin/env python3
"""Parallel git status fetcher using asyncio subprocess."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


async def run_git_command(command: str) -> tuple[str, str, int]:
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
        process.returncode or 0,
    )


async def gather_git_status() -> dict[str, Any]:
    commands = {
        "status": "git status",
        "diff": "git diff",
        "diff_staged": "git diff --staged",
        "log": "git log --oneline -10",
        "current_branch": "git branch --show-current",
    }

    tasks = {key: run_git_command(cmd) for key, cmd in commands.items()}
    results = await asyncio.gather(*[tasks[key] for key in commands.keys()])

    output = {}
    for (key, _), (stdout, stderr, returncode) in zip(commands.items(), results):
        output[key] = {"stdout": stdout, "stderr": stderr, "returncode": returncode, "success": returncode == 0}

    return output


async def main() -> int:
    try:
        check_process = await asyncio.create_subprocess_shell(
            "git rev-parse --git-dir",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _ = await check_process.communicate()

        if check_process.returncode != 0:
            print(json.dumps({"error": "Not a git repository", "cwd": str(Path.cwd())}), file=sys.stderr)
            return 1

        results = await gather_git_status()
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
