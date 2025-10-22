#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "orjson",
# ]
# ///
# pyright: reportMissingImports=false

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

import orjson


class ReadToolInput(TypedDict):
    file_path: str


class WriteToolInput(TypedDict):
    file_path: str
    content: str


class EditToolInput(TypedDict):
    file_path: str
    old_string: str
    new_string: str
    replace_all: NotRequired[bool]


ToolInput = ReadToolInput | WriteToolInput | EditToolInput


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: Literal["Read", "Write", "Edit"]
    transcript_path: str
    cwd: str
    hook_event_name: Literal["post-tool-use"]
    tool_input: ToolInput
    tool_response: dict[str, Any]


class ToolUseBlock(TypedDict):
    type: Literal["tool_use"]
    name: Literal["Read"] | str
    id: str
    input: dict[str, Any]


class ToolResultBlock(TypedDict):
    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[dict[str, Any]]
    is_error: NotRequired[bool]


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


ContentBlock = ToolUseBlock | ToolResultBlock | TextBlock


class AssistantMessage(TypedDict):
    role: Literal["assistant"]
    content: list[ContentBlock]


class UserMessage(TypedDict):
    role: Literal["user"]
    content: str | list[ContentBlock]


class TranscriptEntry(TypedDict):
    type: Literal["user", "assistant"]
    message: AssistantMessage | UserMessage
    timestamp: NotRequired[str]


class RawTranscriptEntry(TypedDict):
    type: str
    message: dict[str, Any]
    timestamp: NotRequired[str]


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

    def _has_guide_been_read(self, guide_path: Path) -> bool:
        if not self.transcript_path.exists():
            return False

        try:
            guide_path_str = str(guide_path)
            transcript_content = self.transcript_path.read_text(encoding="utf-8")

            for line in transcript_content.splitlines():
                if not line.strip():
                    continue

                try:
                    entry: RawTranscriptEntry = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                if not isinstance(entry, dict):
                    continue

                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message")
                if not isinstance(message, dict):
                    continue

                if message.get("role") != "assistant":
                    continue

                content = message.get("content")
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") != "tool_use":
                        continue

                    if block.get("name") != "Read":
                        continue

                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        continue

                    if tool_input.get("file_path") == guide_path_str:
                        return True

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

        if self._has_guide_been_read(guide_path):
            return ""

        file_extension = extension[1:].upper()

        warning_message = (
            f"ACTION REQUIRED: Use Read tool to read guide for {file_extension} immediately. READ NOW."
            f"You MUST read the following guide before proceeding with {path.name}:\n"
            f"{guide_path}\n\n"
        )

        return warning_message


def main() -> None:
    """Main entry point for the hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        input_raw = sys.stdin.read()
        if not input_raw:
            print(f"[{hook_filename}] Skipping: No input provided")
            sys.exit(0)

        data: PostToolUseInput = orjson.loads(input_raw)
    except (orjson.JSONDecodeError, KeyError, TypeError):
        print(f"[{hook_filename}] Skipping: Invalid input format")
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

    checker = LanguageGuideChecker(cwd, transcript_path)
    message = checker.check_and_inject(file_path)

    if message:
        print(message, file=sys.stderr)
        sys.exit(2)

    print(f"[{hook_filename}] Success: No compliance check needed")
    sys.exit(0)


if __name__ == "__main__":
    main()
