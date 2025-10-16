#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NotRequired, TypedDict


class ReadToolInput(TypedDict):
    file_path: str


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: ReadToolInput
    tool_response: dict[str, Any]


class TranscriptMessage(TypedDict):
    type: NotRequired[str]
    content: NotRequired[str | list[dict[str, Any]]]
    message: NotRequired[dict[str, Any]]


class LanguageGuideChecker:
    """Checks language-specific guide compliance when reading code files."""

    MODULAR_PROMPTS_DIR = Path.home() / ".claude" / "modular-prompts" / "languages"

    def __init__(self, cwd: str, transcript_path: str) -> None:
        self.cwd = Path(cwd)
        self.transcript_path = Path(transcript_path)

    def _get_guide_path(self, extension: str) -> Path | None:
        if not extension or not extension.startswith("."):
            return None

        guide_filename = f"{extension[1:]}.md"
        guide_path = self.MODULAR_PROMPTS_DIR / guide_filename

        return guide_path if guide_path.exists() else None

    def _get_guide_content(self, guide_path: Path) -> str | None:
        if not guide_path.exists():
            return None

        try:
            return guide_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _get_guide_identifier(self, guide_content: str, guide_filename: str) -> str:
        return f"[language-guide:{guide_filename}]"

    def _is_guide_in_transcript(self, identifier: str) -> bool:
        if not self.transcript_path.exists():
            return False

        try:
            with self.transcript_path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        entry: TranscriptMessage = json.loads(line)

                        contents_to_check = []

                        if "content" in entry:
                            contents_to_check.append(entry["content"])

                        if "message" in entry and isinstance(entry["message"], dict):
                            msg_content = entry["message"].get("content")
                            if msg_content:
                                contents_to_check.append(msg_content)

                        for content in contents_to_check:
                            if isinstance(content, list):
                                content_str = json.dumps(content)
                            else:
                                content_str = str(content)

                            if identifier in content_str:
                                return True

                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue

            return False

        except OSError:
            return False

    def check_and_inject(self, file_path: str) -> str:
        path = Path(file_path)
        extension = path.suffix

        guide_path = self._get_guide_path(extension)
        if not guide_path:
            return ""

        guide_content = self._get_guide_content(guide_path)
        if not guide_content:
            return ""

        guide_filename = guide_path.name
        identifier = self._get_guide_identifier(guide_content, guide_filename)

        if self._is_guide_in_transcript(identifier):
            return ""

        return f"[language-guide:{guide_filename}]\n{guide_content}"


def main() -> None:
    """Main entry point for the hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        input_raw = sys.stdin.read()
        if not input_raw:
            print(f"[{hook_filename}] Skipping: No input provided")
            sys.exit(0)

        data: PostToolUseInput = json.loads(input_raw)
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"[{hook_filename}] Skipping: Invalid input format")
        sys.exit(0)

    tool_name = data["tool_name"]

    if tool_name not in ("Read", "Write", "Edit", "MultiEdit"):
        print(f"[{hook_filename}] Skipping: Tool {tool_name} not relevant")
        sys.exit(0)

    tool_input = data["tool_input"]
    file_path = tool_input.get("file_path", "")

    if not file_path:
        print(f"[{hook_filename}] Skipping: No file path provided")
        sys.exit(0)

    if Path(file_path).resolve() == Path(__file__).resolve():
        print(f"[{hook_filename}] Skipping: Self-reference detected")
        sys.exit(0)

    cwd = data["cwd"]
    transcript_path = data["transcript_path"]

    print(f"[{hook_filename}] DEBUG: transcript_path = {transcript_path}", file=sys.stderr)
    print(f"[{hook_filename}] DEBUG: transcript exists = {Path(transcript_path).exists()}", file=sys.stderr)

    checker = LanguageGuideChecker(cwd, transcript_path)
    message = checker.check_and_inject(file_path)

    if message:
        print(message, file=sys.stderr)
        sys.exit(2)

    print(f"[{hook_filename}] Success: No compliance check needed")
    sys.exit(0)


if __name__ == "__main__":
    main()
