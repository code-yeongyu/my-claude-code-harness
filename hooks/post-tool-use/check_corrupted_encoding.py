#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "chardet>=5.2.0",
# ]
# ///
"""
Post-tool-use corrupted encoding checker for Claude Code.
Checks files after they've been written/edited for encoding issues and reports warnings.
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, NoReturn, TypedDict

import chardet  # pyright: ignore[reportMissingImports]


class Config:
    """Configuration constants for the hook."""

    EXIT_CODE_WARNING: int = 2
    EXIT_CODE_SUCCESS: int = 0
    REPLACEMENT_CHAR = "\ufffd"


class Violation(TypedDict):
    line: int
    text: str
    issue_type: str
    full_line: str


class EditOperation(TypedDict):
    old_string: str
    new_string: str


class WriteToolInput(TypedDict):
    file_path: str
    content: str


class EditToolInput(TypedDict):
    file_path: str
    old_string: str
    new_string: str


class MultiEditToolInput(TypedDict):
    file_path: str
    edits: list[EditOperation]


class NotebookEditToolInput(TypedDict):
    notebook_path: str
    new_source: str


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: ClaudeCodeToolInput
    tool_response: dict[str, Any]


ClaudeCodeToolInput = WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput


def main() -> None:
    """Main entry point for the post-tool-use encoding checker."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception as e:
        _handle_hook_error(e)


def _execute_hook_pipeline() -> NoReturn:
    """Execute the main hook logic pipeline."""
    data = parse_input()
    if not should_process(data):
        sys.exit(Config.EXIT_CODE_SUCCESS)

    file_path = extract_file_path(data)
    if not file_path or not Path(file_path).exists():
        print(f"[check-corrupted-encoding] Skipping: File not found or no valid file path: {file_path}")
        sys.exit(Config.EXIT_CODE_SUCCESS)

    violations = check_file_encoding(file_path)
    handle_findings(violations, file_path)


def parse_input() -> PostToolUseInput:
    """Parse and validate stdin input."""
    input_raw = sys.stdin.read()
    if not input_raw.strip():
        sys.exit(Config.EXIT_CODE_SUCCESS)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error parsing input: {e}", file=sys.stderr)
        sys.exit(Config.EXIT_CODE_SUCCESS)


def should_process(data: PostToolUseInput) -> bool:
    """Determine if the input should be processed."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input")

    if not tool_name or not tool_input:
        return False

    if not isinstance(tool_input, dict):
        return False

    if tool_name not in ["Write", "Edit", "MultiEdit", "NotebookEdit"]:
        return False

    file_path = extract_file_path(data)
    if not file_path:
        return False

    return True


def extract_file_path(data: PostToolUseInput) -> str:
    """Extract file path from input data."""
    tool_input = data.get("tool_input", {})
    if isinstance(tool_input, dict):
        if "file_path" in tool_input:
            return tool_input["file_path"]  # type: ignore[literal-required]
        elif "notebook_path" in tool_input:
            return tool_input["notebook_path"]  # type: ignore[literal-required]
        elif "target_file" in tool_input:
            return tool_input["target_file"]  # type: ignore[typeddict-item]
    return ""


def check_file_encoding(file_path: str) -> list[Violation]:
    """Check the file for encoding issues by reading it from disk."""
    try:
        path_obj = Path(file_path)
        if not path_obj.exists():
            return []

        # #given: Check if file is binary using 'file' command
        file_type_result = subprocess.run(
            ["file", "--mime-type", "--brief", str(path_obj)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        # #when: file command detects binary or non-text MIME type
        mime_type = file_type_result.stdout.strip()
        if file_type_result.returncode == 0:
            # #then: Report as binary/corrupted if not text
            if not mime_type.startswith("text/") and mime_type not in ["inode/x-empty", "application/json"]:
                return [
                    Violation(
                        line=1,
                        text=f"Binary/corrupted file detected (MIME: {mime_type})",
                        issue_type="binary_file",
                        full_line="<binary data>",
                    )
                ]

        # #given: Read file bytes to check for null bytes (strong binary indicator)
        raw_bytes = path_obj.read_bytes()

        # #when: File contains null bytes
        if b"\x00" in raw_bytes:
            # #then: Report as binary
            return [
                Violation(
                    line=1,
                    text="File contains null bytes (binary file)",
                    issue_type="null_bytes",
                    full_line="<binary data>",
                )
            ]

        # #given: Try UTF-8 decoding with error tracking
        try:
            content = raw_bytes.decode("utf-8")
            # #when: Successful UTF-8 decode but contains replacement chars
            if Config.REPLACEMENT_CHAR in content:
                # #then: Report corruption (replacement chars indicate decode issues)
                replacement_count = content.count(Config.REPLACEMENT_CHAR)
                return [
                    Violation(
                        line=1,
                        text=f"UTF-8 decode succeeded but contains {replacement_count} replacement characters",
                        issue_type="replacement_chars_in_decode",
                        full_line=content.split("\n")[0][:100] if content else "",
                    )
                ]
        except UnicodeDecodeError:
            # #when: UTF-8 decode fails
            # #then: Try chardet to identify encoding
            detection_result = chardet.detect(raw_bytes)
            if detection_result["encoding"]:
                try:
                    _content = raw_bytes.decode(detection_result["encoding"])
                except (UnicodeDecodeError, LookupError):
                    return [
                        Violation(
                            line=1,
                            text=f"Failed to decode file with detected encoding: {detection_result['encoding']}",
                            issue_type="decode_error",
                            full_line="<binary data>",
                        )
                    ]
            else:
                return [
                    Violation(
                        line=1,
                        text="Could not detect file encoding",
                        issue_type="unknown_encoding",
                        full_line="<binary data>",
                    )
                ]

        return []

    except OSError as e:
        return [
            Violation(
                line=1,
                text=f"Error reading file: {e}",
                issue_type="read_error",
                full_line="",
            )
        ]


def handle_findings(violations: list[Violation], file_path: str) -> NoReturn:
    """Handle detected violations and exit appropriately."""
    if not violations:
        print("[check-corrupted-encoding] Success: No encoding issues found")
        sys.exit(Config.EXIT_CODE_SUCCESS)

    display_path = _get_display_path(file_path)
    message = (
        f"\n<encoding-warning>\n"
        f"⚠️ ENCODING ISSUES DETECTED: {display_path}\n"
        f"The file contains encoding issues ({len(violations)} problems found).\n"
        f"Please use Read() to review the file and rewrite it from scratch.\n"
        f"</encoding-warning>\n"
    )

    print(message, file=sys.stderr)
    sys.exit(Config.EXIT_CODE_WARNING)


def _get_display_path(file_path: str) -> str:
    """Get display-friendly path relative to cwd if possible."""
    cwd = Path.cwd()
    try:
        path_obj = Path(file_path).resolve()
        if path_obj.is_relative_to(cwd):
            relative_path = path_obj.relative_to(cwd)
            result = str(relative_path)
            return result
        return file_path
    except (ValueError, OSError):
        return file_path


def _handle_hook_error(e: Exception) -> NoReturn:
    """Handle errors in hook execution."""
    print(f"ERROR in encoding check hook: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(Config.EXIT_CODE_SUCCESS)


if __name__ == "__main__":
    main()
