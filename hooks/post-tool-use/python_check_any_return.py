#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ast-grep-py>=0.24.1",
# ]
# ///
"""
Any return type detector hook for Claude Code (PostToolUse version).
Detects '-> Any' return type hints after file modifications and provides feedback to Claude for correction.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from re import Pattern
from typing import (
    Any,
    Literal,
    NoReturn,
    TypedDict,
)

import ast_grep_py as sg

TYPE_IGNORE_PATTERN: Pattern[str] = re.compile(r"#\s*type:\s*ignore(?:\[[\w,\s]+\])?(?:\s|$)")


EXIT_CODE_BLOCK_TOOL: int = 2
DEFAULT_TEXT_TRUNCATION: int = 80
LONG_TEXT_TRUNCATION: int = 120
TAB: str = "\t"


class AnyReturnIssue(TypedDict):
    type: Literal["any_return", "any_optional_return"]
    issue_description: str
    suggestion: str
    line: int
    column: int
    end_line: int
    end_column: int
    function_name: str
    text: str
    is_type_ignored: bool


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


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput
    tool_response: dict[str, Any]


class AnyReturnContext:
    """Context for creating Any return type issues."""

    def __init__(
        self,
        issue_type: Literal["any_return", "any_optional_return"],
        node: sg.SgNode,
        function_name: str,
        is_type_ignored: bool,
    ) -> None:
        self.issue_type: Literal["any_return", "any_optional_return"] = issue_type
        self.node: sg.SgNode = node
        self.function_name: str = function_name
        self.is_type_ignored: bool = is_type_ignored


def run_any_return_check() -> None:
    """Main entry point for the PostToolUse hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception:
        _handle_hook_error()


def detect_any_return_violations(code: str) -> list[AnyReturnIssue]:
    """Detect all '-> Any' return type violations in the given code."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    node: sg.SgNode = root.root()
    source_lines: list[str] = code.split("\n")

    violations: list[AnyReturnIssue] = []

    # Pattern for direct -> Any return type
    any_return_violations: list[AnyReturnIssue] = _detect_any_returns(node, source_lines)
    violations.extend(any_return_violations)

    # Pattern for Optional[Any] or Any | None return types
    optional_any_violations: list[AnyReturnIssue] = _detect_optional_any_returns(node, source_lines)
    violations.extend(optional_any_violations)

    # Filter out type-ignored violations
    filtered_violations: list[AnyReturnIssue] = [
        violation for violation in violations if not violation["is_type_ignored"]
    ]
    return filtered_violations


def build_warning_message(violations: list[AnyReturnIssue], file_path: str) -> str:
    """Build warning message for Any return type violations."""
    if not violations:
        return ""

    display_path: str = _get_display_path(file_path)

    msg = f"""ANY RETURN TYPE DETECTED - CRITICAL WARNING

File: {display_path}
Found {len(violations)} '-> Any' return type{" violations" if len(violations) > 1 else " violation"}.

Using '-> Any' as a return type hint is not allowed.
All functions must have specific return type hints.

Detected violations:
"""

    for i, violation in enumerate(violations, 1):
        msg += f"\n[{i}] Line {violation['line']}: {violation['issue_description']}\n"
        msg += f"\t{violation['suggestion']}\n"

    msg += "\nFIX IMMEDIATELY: Replace all '-> Any' with specific type hints."

    return msg


def _read_file_content(file_path: str) -> str | None:
    """Read file content with error handling."""
    if not file_path or not Path(file_path).exists():
        return None

    try:
        with open(file_path, encoding="utf-8") as file_handle:
            return file_handle.read()
    except Exception:
        return None


def parse_input() -> PostToolUseInput:
    """Parse and validate stdin input."""
    input_raw: str = sys.stdin.read()
    if not input_raw:
        print("[check-any-return] Skipping: No input provided")
        sys.exit(0)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, KeyError, TypeError):
        print("[check-any-return] Skipping: Invalid input format")
        sys.exit(0)


def should_process(data: PostToolUseInput) -> bool:
    """Determine if the input should be processed."""
    tool_input = data["tool_input"]
    tool_response = data["tool_response"]

    if not isinstance(tool_input, dict) or not isinstance(tool_response, dict):
        return False

    success = tool_response.get("success")
    if success is None:
        type_field = tool_response.get("type")
        match type_field:
            case "create" | "edit" | "update":
                success = True
            case _ if "filePath" in tool_response or "structuredPatch" in tool_response:
                success = True
            case _:
                success = False

    if not success:
        return False

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not file_path or not file_path.endswith(".py"):
        return False

    excluded: bool = _is_excluded_path(file_path)
    return not excluded


def process_tool_input(data: PostToolUseInput) -> list[AnyReturnIssue]:
    """Process tool input and detect violations in newly added content."""
    tool_name = data["tool_name"]
    tool_input = data["tool_input"]

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not isinstance(tool_input, dict):
        return []

    new_violations: list[AnyReturnIssue] = []
    existing_violations: set[str] = _get_existing_violations(file_path, tool_name, tool_input)

    # Read the actual file content after modification
    content = _read_file_content(file_path)
    if content is None:
        return []

    all_violations = detect_any_return_violations(content)

    for violation in all_violations:
        violation_key = _create_violation_key(violation)
        if violation_key not in existing_violations:
            new_violations.append(violation)

    return new_violations


def handle_findings(violations: list[AnyReturnIssue], file_path: str) -> NoReturn:
    """Handle detected violations and provide feedback to Claude."""
    if not violations:
        print("[check-any-return] Success: No Any return type violations found")
        sys.exit(0)

    message: str = build_warning_message(violations, file_path)

    if message.strip():
        print(message, file=sys.stderr)
        sys.exit(EXIT_CODE_BLOCK_TOOL)

    sys.exit(0)


def extract_file_path(data: PostToolUseInput) -> str:
    """Extract file path from input data."""
    tool_input = data["tool_input"]
    if isinstance(tool_input, dict) and "file_path" in tool_input:
        return tool_input["file_path"]  # type: ignore[literal-required]
    return ""


def _execute_hook_pipeline() -> NoReturn:
    """Execute the main hook logic pipeline."""
    data: PostToolUseInput = parse_input()
    if not should_process(data):
        print("[check-any-return] Skipping: File not eligible for processing")
        sys.exit(0)
    violations: list[AnyReturnIssue] = process_tool_input(data)
    file_path: str = extract_file_path(data)
    handle_findings(violations, file_path)


def _handle_hook_error() -> NoReturn:
    """Handle errors in hook execution."""
    print("[check-any-return] Skipping: Unexpected error occurred")
    sys.exit(0)


def _is_excluded_path(file_path: str) -> bool:
    """Check if file path should be excluded from processing."""
    # Exclude hook files themselves
    if "/hooks/" in file_path:
        return True

    # Exclude test files (they might use Any for mocking)
    if (
        "/test/" in file_path
        or "/tests/" in file_path
        or file_path.endswith("_test.py")
        or "test_" in Path(file_path).name
    ):
        return True

    return False


def _detect_any_returns(node: sg.SgNode, source_lines: list[str]) -> list[AnyReturnIssue]:
    """Detect direct '-> Any' return type annotations."""
    violations: list[AnyReturnIssue] = []

    # Pattern for functions with -> Any return type
    # Matches: def func() -> Any:
    function_patterns = [
        "def $FUNC($$$PARAMS) -> Any:",
        "async def $FUNC($$$PARAMS) -> Any:",
    ]

    for pattern in function_patterns:
        function_nodes: list[sg.SgNode] = node.find_all(pattern=pattern)

        for function_node in function_nodes:
            function_name = _extract_function_name(function_node)
            is_type_ignored = _is_type_ignored(function_node, source_lines)

            context = AnyReturnContext(
                issue_type="any_return",
                node=function_node,
                function_name=function_name,
                is_type_ignored=is_type_ignored,
            )
            issue = _create_any_return_issue(context)
            violations.append(issue)

    return violations


def _detect_optional_any_returns(node: sg.SgNode, source_lines: list[str]) -> list[AnyReturnIssue]:
    """Detect Optional[Any] and Any | None return type annotations."""
    violations: list[AnyReturnIssue] = []

    # Patterns for Optional[Any] and Any | None
    optional_patterns = [
        "def $FUNC($$$PARAMS) -> Optional[Any]:",
        "async def $FUNC($$$PARAMS) -> Optional[Any]:",
        "def $FUNC($$$PARAMS) -> Any | None:",
        "async def $FUNC($$$PARAMS) -> Any | None:",
        "def $FUNC($$$PARAMS) -> None | Any:",
        "async def $FUNC($$$PARAMS) -> None | Any:",
    ]

    for pattern in optional_patterns:
        function_nodes: list[sg.SgNode] = node.find_all(pattern=pattern)

        for function_node in function_nodes:
            function_name = _extract_function_name(function_node)
            is_type_ignored = _is_type_ignored(function_node, source_lines)

            context = AnyReturnContext(
                issue_type="any_optional_return",
                node=function_node,
                function_name=function_name,
                is_type_ignored=is_type_ignored,
            )
            issue = _create_any_return_issue(context)
            violations.append(issue)

    return violations


def _create_any_return_issue(context: AnyReturnContext) -> AnyReturnIssue:
    """Create an AnyReturnIssue object from context."""
    start: Any = context.node.range().start
    end: Any = context.node.range().end

    issue_description: str
    suggestion: str

    if context.issue_type == "any_return":
        issue_description = f"Function '{context.function_name}' has '-> Any' return type"
        suggestion = "Replace '-> Any' with a specific type hint (e.g., str, int, dict, List[str], etc.)"
    else:
        issue_description = f"Function '{context.function_name}' has Optional[Any] or Any | None return type"
        suggestion = "Replace Optional[Any] with a specific optional type (e.g., Optional[str], str | None, etc.)"

    # Get the function signature line
    function_text = context.node.text()
    first_line = function_text.split("\n")[0] if "\n" in function_text else function_text

    issue: AnyReturnIssue = AnyReturnIssue(
        type=context.issue_type,
        issue_description=issue_description,
        suggestion=suggestion,
        line=start.line + 1,
        column=start.column,
        end_line=end.line + 1,
        end_column=end.column,
        function_name=context.function_name,
        text=_truncate_text(first_line, DEFAULT_TEXT_TRUNCATION),
        is_type_ignored=context.is_type_ignored,
    )
    return issue


def _extract_function_name(function_node: sg.SgNode) -> str:
    """Extract function name from a function node."""
    func_match: sg.SgNode | None = function_node.get_match("FUNC")
    if func_match:
        return func_match.text()

    # Try to get the name field directly
    name_node: sg.SgNode | None = function_node.field("name")
    if name_node:
        return name_node.text()

    return "unknown"


def _is_type_ignored(function_node: sg.SgNode, source_lines: list[str]) -> bool:
    """Check if function has # type: ignore comment."""
    function_line_num: int = function_node.range().start.line
    if function_line_num < len(source_lines):
        function_line: str = source_lines[function_line_num]
        if TYPE_IGNORE_PATTERN.search(function_line):
            return True
    return False


def _get_display_path(file_path: str) -> str:
    """Get display-friendly path relative to cwd if possible."""
    cwd: Path = Path.cwd()
    try:
        path_obj: Path = Path(file_path).resolve()
        if path_obj.is_relative_to(cwd):
            relative_path: Path = path_obj.relative_to(cwd)
            result: str = str(relative_path)
            return result
        return file_path
    except (ValueError, OSError):
        return file_path


def _truncate_text(text: str, max_length: int = DEFAULT_TEXT_TRUNCATION) -> str:
    """Truncate text to specified length with ellipsis if needed."""
    if len(text) <= max_length:
        return text

    lines: list[str] = text.split("\n")
    if len(lines) > 1:
        first_line: str = lines[0]
        if len(first_line) > max_length - 3:
            truncated: str = first_line[: max_length - 3] + "..."
            return truncated
        return first_line + "..."

    truncated_text: str = text[: max_length - 3] + "..."
    return truncated_text


def _get_existing_violations(
    file_path: str, tool_name: str, tool_input: WriteToolInput | EditToolInput | MultiEditToolInput
) -> set[str]:
    """Get existing Any return violations from old content to avoid duplicate warnings."""
    existing_violations: set[str] = set()

    if tool_name == "Edit":
        if file_path and Path(file_path).exists():
            try:
                old_string = tool_input["old_string"]  # type: ignore[literal-required]
                new_string = tool_input["new_string"]  # type: ignore[literal-required]

                with open(file_path, encoding="utf-8") as file_handle:
                    current_content: str = file_handle.read()

                if old_string and new_string and new_string in current_content:
                    pre_edit_content: str = current_content.replace(new_string, old_string, 1)
                    old_violations = detect_any_return_violations(pre_edit_content)
                    for violation in old_violations:
                        existing_violations.add(_create_violation_key(violation))
            except Exception:
                pass

    elif tool_name == "MultiEdit":
        if file_path and Path(file_path).exists():
            try:
                with open(file_path, encoding="utf-8") as file_handle:
                    current_content: str = file_handle.read()

                pre_edit_content: str = current_content
                edits = tool_input["edits"]  # type: ignore[literal-required]

                for edit in reversed(edits):
                    if isinstance(edit, dict):
                        edit_old_string = edit["old_string"]
                        edit_new_string = edit["new_string"]
                        if edit_old_string and edit_new_string and edit_new_string in pre_edit_content:
                            pre_edit_content = pre_edit_content.replace(edit_new_string, edit_old_string, 1)

                old_violations = detect_any_return_violations(pre_edit_content)
                for violation in old_violations:
                    existing_violations.add(_create_violation_key(violation))
            except Exception:
                pass

    # For Write tool, there are no existing violations since it's a new file

    return existing_violations


def _create_violation_key(violation: AnyReturnIssue) -> str:
    """Create a unique key for a violation to detect duplicates."""
    return f"{violation['function_name']}:{violation['line']}:{violation['type']}"


if __name__ == "__main__":
    run_any_return_check()
