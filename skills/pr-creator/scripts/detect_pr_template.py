#!/usr/bin/env python3
"""Detect PR template in git repository with worktree support."""

import asyncio
import json
import sys
from pathlib import Path


async def run_git_command(command: str) -> tuple[str, int]:
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return stdout.decode("utf-8").strip(), process.returncode or 0


async def find_pr_template() -> dict[str, str | bool | None]:
    git_root_output, returncode = await run_git_command("git rev-parse --show-toplevel")

    if returncode != 0:
        return {"error": "Not a git repository", "found": False}

    git_root = Path(git_root_output)

    template_locations = [
        git_root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        git_root / ".github" / "pull_request_template.md",
        git_root / "docs" / "PULL_REQUEST_TEMPLATE.md",
        git_root / "PULL_REQUEST_TEMPLATE.md",
    ]

    for template_path in template_locations:
        if template_path.exists() and template_path.is_file():
            content = template_path.read_text(encoding="utf-8")
            return {"found": True, "path": str(template_path), "content": content}

    return {"found": False}


async def main() -> int:
    try:
        result = await find_pr_template()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__, "found": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
