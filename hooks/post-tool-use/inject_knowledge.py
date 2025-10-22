#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "orjson",
# ]
# ///
# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

import orjson


class EditOperation(TypedDict):
    old_string: str
    new_string: str


class WriteToolInput(TypedDict):
    file_path: str
    content: str


class MultiEditToolInput(TypedDict):
    file_path: str
    edits: list[EditOperation]


class EditToolInput(TypedDict):
    file_path: str
    old_string: str
    new_string: str


class NotebookEditToolInput(TypedDict):
    notebook_path: str
    new_source: str


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: (
        WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput
    )
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


class KnowledgeInfo(TypedDict):
    path: str
    distance: int
    type: str
    hash: str
    last_modified: str


class KnowledgeFinder:
    KNOWLEDGE_FILES = ["claude.md", "agents.md", "readme.md"]

    def __init__(self, file_path: str, cwd: str | None = None) -> None:
        self.file_path = Path(file_path).resolve()
        self.project_root = Path(cwd).resolve() if cwd else self._find_project_root()

    def _find_project_root(self) -> Path:
        markers = [".git", "pyproject.toml", "package.json", ".venv"]
        current = self.file_path.parent

        while current != current.parent:
            for marker in markers:
                if (current / marker).exists():
                    return current
            current = current.parent

        return self.file_path.parent

    def find_knowledge_files(self) -> list[KnowledgeInfo]:
        if not self.project_root:
            return []

        try:
            self.file_path.relative_to(self.project_root)
        except ValueError:
            return []

        knowledge_infos: list[KnowledgeInfo] = []
        seen_files: set[str] = set()
        current_dir = self.file_path.parent
        distance = 0

        while current_dir >= self.project_root and current_dir != current_dir.parent:
            # Skip project root as it's automatically read by the system
            if current_dir != self.project_root:
                for file in current_dir.iterdir():
                    if file.is_file() and file.name.lower() in self.KNOWLEDGE_FILES:
                        try:
                            relative_path = file.relative_to(self.project_root)
                            path_str = str(relative_path)
                        except ValueError:
                            path_str = str(file)

                        normalized_path = path_str.lower()
                        if normalized_path not in seen_files:
                            seen_files.add(normalized_path)
                            file_type = (
                                "claude" if "claude" in file.name.lower() else "agents"
                            )

                            try:
                                file_content = file.read_bytes()
                                file_hash = hashlib.sha256(file_content).hexdigest()[
                                    :16
                                ]
                                last_modified = datetime.fromtimestamp(
                                    file.stat().st_mtime
                                ).isoformat()
                            except OSError:
                                file_hash = "unknown"
                                last_modified = "unknown"

                            knowledge_infos.append(
                                KnowledgeInfo(
                                    path=path_str,
                                    distance=distance,
                                    type=file_type,
                                    hash=file_hash,
                                    last_modified=last_modified,
                                )
                            )

            if current_dir == self.project_root:
                break

            current_dir = current_dir.parent
            distance += 1

        return sorted(knowledge_infos, key=lambda x: x["distance"])


class KnowledgeInjector:
    def __init__(self, project_root: Path, transcript_path: str) -> None:
        self.project_root = project_root
        self.transcript_path = Path(transcript_path)

    def _has_knowledge_been_read(self, knowledge_path: Path) -> bool:
        if not self.transcript_path.exists():
            return False

        try:
            knowledge_path_str = str(knowledge_path)
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

                    if tool_input.get("file_path") == knowledge_path_str:
                        return True

            return False

        except OSError:
            return False

    def check_and_inject(self, knowledge_info: KnowledgeInfo) -> str:
        path = knowledge_info["path"]
        knowledge_type = knowledge_info["type"]

        full_path = self.project_root / path
        if not full_path.exists():
            return ""

        if self._has_knowledge_been_read(full_path):
            return ""

        file_type_display = knowledge_type.upper()

        warning_message = (
            f"ACTION REQUIRED: Use Read tool to read {file_type_display} knowledge immediately. READ NOW.\n"
            f"You MUST read the following knowledge file before proceeding:\n"
            f"{full_path}\n\n"
        )

        return warning_message


class HookHandler:
    def handle(self) -> None:
        hook_filename = Path(__file__).stem.replace("_", "-")

        try:
            input_raw = sys.stdin.read()
            if not input_raw:
                print(f"[{hook_filename}] Skipping: No input provided")
                sys.exit(0)

            data: PostToolUseInput = orjson.loads(input_raw)
        except (orjson.JSONDecodeError, KeyError, TypeError):
            print(f"[{hook_filename}] Skipping: Invalid input format")
            sys.exit(0)

        tool_name = data["tool_name"]
        tool_input = data["tool_input"]

        if tool_name not in [
            "Read",
            "Write",
            "Edit",
            "MultiEdit",
            "NotebookEdit",
        ]:
            print(f"[{hook_filename}] Skipping: Tool {tool_name} not relevant")
            sys.exit(0)

        file_path = ""
        if "file_path" in tool_input:
            file_path = tool_input["file_path"]  # type: ignore[literal-required]
        elif "notebook_path" in tool_input:
            file_path = tool_input["notebook_path"]  # type: ignore[literal-required]

        if not file_path:
            print(f"[{hook_filename}] Skipping: No file path provided")
            sys.exit(0)

        if Path(file_path).resolve() == Path(__file__).resolve():
            print(f"[{hook_filename}] Skipping: Self-reference detected")
            sys.exit(0)

        cwd = data["cwd"]
        transcript_path = data["transcript_path"]
        finder = KnowledgeFinder(file_path, cwd)

        knowledge_infos = finder.find_knowledge_files()
        if not knowledge_infos:
            print(f"[{hook_filename}] Success: No knowledge files found")
            sys.exit(0)

        current_file = Path(file_path)
        try:
            project_root = finder.project_root
            current_relative = current_file.relative_to(project_root)
            current_path_str = str(current_relative)
        except ValueError:
            current_path_str = str(current_file)

        filtered_knowledge_infos = [
            info
            for info in knowledge_infos
            if info["path"].lower() != current_path_str.lower()
        ]

        if not filtered_knowledge_infos:
            print(f"[{hook_filename}] Success: No relevant knowledge files found")
            sys.exit(0)

        injector = KnowledgeInjector(finder.project_root, transcript_path)
        messages_to_inject: list[str] = []

        for knowledge_info in filtered_knowledge_infos:
            message = injector.check_and_inject(knowledge_info)
            if message:
                messages_to_inject.append(message)

        if messages_to_inject:
            combined_message = "\n\n".join(messages_to_inject)
            print(combined_message, file=sys.stderr)
            sys.exit(2)

        print(f"[{hook_filename}] Success: All knowledge files already in context")
        sys.exit(0)


def main() -> None:
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    handler = HookHandler()
    handler.handle()


if __name__ == "__main__":
    main()
