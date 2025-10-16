#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ast-grep-py>=0.24.1",
# ]
# ///
"""
TypedDict total=False detector hook for Claude Code (PostToolUse version).
Detects TypedDict declarations with total=False and suggests using NotRequired instead (Python 3.11+).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import (
    Any,
    Literal,
    NoReturn,
    TypedDict,
)

import ast_grep_py as sg


def main() -> None:
    """Main entry point for the PostToolUse hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception:
        _handle_hook_error()


EXIT_CODE_BLOCK_TOOL: int = 2
DEFAULT_TEXT_TRUNCATION: int = 80
LONG_TEXT_TRUNCATION: int = 120
TAB: str = "\t"


class TotalFalseIssue(TypedDict):
    type: Literal["class_definition", "function_call"]
    issue_description: str
    suggestion: str
    line: int
    column: int
    end_line: int
    end_column: int
    class_name: str
    text: str


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
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput
    tool_response: dict[str, Any]


class TotalFalseContext:
    """Context for creating total=False issues."""

    def __init__(
        self,
        issue_type: Literal["class_definition", "function_call"],
        node: sg.SgNode,
        class_name: str,
    ) -> None:
        self.issue_type: Literal["class_definition", "function_call"] = issue_type
        self.node: sg.SgNode = node
        self.class_name: str = class_name


def detect_total_false_violations(code: str) -> list[TotalFalseIssue]:
    """Detect all TypedDict total=False violations in the given code."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    node: sg.SgNode = root.root()

    violations: list[TotalFalseIssue] = []

    # Pattern 1: class MyDict(TypedDict, total=False)
    class_violations: list[TotalFalseIssue] = _detect_class_definitions(node)
    violations.extend(class_violations)

    # Pattern 2: MyDict = TypedDict('MyDict', {...}, total=False)
    call_violations: list[TotalFalseIssue] = _detect_function_calls(node)
    violations.extend(call_violations)

    return violations


def build_warning_message(violations: list[TotalFalseIssue], file_path: str) -> str:
    """Build warning message for TypedDict total=False violations."""
    if not violations:
        return ""

    display_path: str = _get_display_path(file_path)

    msg = f"""TYPEDDICT total=False DETECTED - MODERNIZATION OPPORTUNITY

File: {display_path}
Found {len(violations)} TypedDict declaration{"s" if len(violations) > 1 else ""} using total=False.

Python 3.11+ introduces NotRequired from typing module, which provides a cleaner,
more explicit way to mark individual fields as optional in TypedDict.

Using NotRequired is preferred over total=False because:
- It makes the intent clearer at the field level
- It follows modern Python typing best practices
- It provides better IDE support and type checking

Detected violations:
"""

    for i, violation in enumerate(violations, 1):
        msg += f"\n[{i}] Line {violation['line']}: {violation['issue_description']}\n"

        msg += "\nCurrent code:\n```python\n"
        msg += _format_code_with_line_numbers(violation["text"], violation["line"])
        msg += "\n```\n"

        msg += "\nSuggested fix:\n```python\n"
        msg += _generate_suggested_fix(violation)
        msg += "\n```\n"

    msg += "\nFIX IMMEDIATELY: Convert all TypedDict declarations with total=False to use NotRequired.\n"
    msg += "This is the modern Python way and follows best practices for Python 3.11+."

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
        print("[check-typeddict-total-false] Skipping: No input provided")
        sys.exit(0)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, KeyError, TypeError):
        print("[check-typeddict-total-false] Skipping: Invalid input format")
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

    if not _check_python_version_311_or_higher():
        return False

    excluded: bool = _is_excluded_path(file_path)
    return not excluded


def process_tool_input(data: PostToolUseInput) -> list[TotalFalseIssue]:
    """Process tool input and detect violations in newly added content."""
    tool_name = data["tool_name"]
    tool_input = data["tool_input"]

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not isinstance(tool_input, dict):
        return []

    new_violations: list[TotalFalseIssue] = []
    existing_violations: set[str] = _get_existing_violations(file_path, tool_name, tool_input)

    content = _read_file_content(file_path)
    if content is None:
        return []

    all_violations = detect_total_false_violations(content)

    for violation in all_violations:
        violation_key = _create_violation_key(violation)
        if violation_key not in existing_violations:
            new_violations.append(violation)

    return new_violations


def handle_findings(violations: list[TotalFalseIssue], file_path: str) -> NoReturn:
    """Handle detected violations and provide feedback to Claude."""
    if not violations:
        print("[check-typeddict-total-false] Success: No TypedDict total=False violations found")
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
        print("[check-typeddict-total-false] Skipping: File not eligible for processing")
        sys.exit(0)
    violations: list[TotalFalseIssue] = process_tool_input(data)
    file_path: str = extract_file_path(data)
    handle_findings(violations, file_path)


def _handle_hook_error() -> NoReturn:
    """Handle errors in hook execution."""
    print("[check-typeddict-total-false] Skipping: Unexpected error occurred")
    sys.exit(0)


def _is_excluded_path(file_path: str) -> bool:
    return False


def _check_python_version_311_or_higher() -> bool:
    """Check if Python version is 3.11 or higher."""
    if sys.version_info.major > 3 or (sys.version_info.major == 3 and sys.version_info.minor >= 11):
        return True

    pyproject_path = Path("pyproject.toml")
    if pyproject_path.exists():
        try:
            with open(pyproject_path) as f:
                content = f.read()

            match = re.search(r'requires-python\s*=\s*"[^"]*3\.(\d+)', content)
            if match:
                minor = int(match.group(1))
                return minor >= 11

            match = re.search(r'target-version\s*=\s*"py3(\d+)"', content)
            if match:
                minor = int(match.group(1))
                return minor >= 11
        except Exception:
            pass

    return False


def _detect_class_definitions(node: sg.SgNode) -> list[TotalFalseIssue]:
    """Detect class MyDict(TypedDict, total=False) patterns."""
    violations: list[TotalFalseIssue] = []

    class_nodes: list[sg.SgNode] = node.find_all(pattern="class $NAME($$$BASES): $BODY")

    for class_node in class_nodes:
        class_text = class_node.text()

        # Pattern: class NAME(TypedDict, total=False) or class NAME(typing.TypedDict, total=False)
        if (
            "TypedDict" in class_text
            and "total=False" in class_text
            and re.search(
                r"class\s+\w+\s*\([^)]*TypedDict[^)]*,\s*total\s*=\s*False[^)]*\)",
                class_text,
            )
        ):
            class_name = _extract_class_name(class_node)
            context = TotalFalseContext(
                issue_type="class_definition",
                node=class_node,
                class_name=class_name,
            )
            issue = _create_total_false_issue(context)
            violations.append(issue)

    return violations


def _detect_function_calls(node: sg.SgNode) -> list[TotalFalseIssue]:
    """Detect MyDict = TypedDict('MyDict', {...}, total=False) patterns."""
    violations: list[TotalFalseIssue] = []

    typeddict_calls: list[sg.SgNode] = node.find_all(pattern="TypedDict($$$ARGS)")

    for call_node in typeddict_calls:
        call_text = call_node.text()

        if "total=False" in call_text:
            parent = call_node.parent()
            class_name = "UnknownDict"

            if parent and parent.kind() == "assignment":
                assignment_text = parent.text()
                match = re.match(r"(\w+)\s*=", assignment_text)
                if match:
                    class_name = match.group(1)

            context = TotalFalseContext(
                issue_type="function_call",
                node=call_node,
                class_name=class_name,
            )
            issue = _create_total_false_issue(context)
            violations.append(issue)

    typing_typeddict_calls: list[sg.SgNode] = node.find_all(pattern="typing.TypedDict($$$ARGS)")

    for call_node in typing_typeddict_calls:
        call_text = call_node.text()

        if "total=False" in call_text:
            parent = call_node.parent()
            class_name = "UnknownDict"

            if parent and parent.kind() == "assignment":
                assignment_text = parent.text()
                match = re.match(r"(\w+)\s*=", assignment_text)
                if match:
                    class_name = match.group(1)

            context = TotalFalseContext(
                issue_type="function_call",
                node=call_node,
                class_name=class_name,
            )
            issue = _create_total_false_issue(context)
            violations.append(issue)

    return violations


def _create_total_false_issue(context: TotalFalseContext) -> TotalFalseIssue:
    """Create a TotalFalseIssue object from context."""
    start: Any = context.node.range().start
    end: Any = context.node.range().end

    issue_description: str
    suggestion: str

    if context.issue_type == "class_definition":
        issue_description = f"TypedDict class '{context.class_name}' uses total=False"
        suggestion = f"Remove total=False and use NotRequired for optional fields in '{context.class_name}'"
    else:
        issue_description = f"TypedDict '{context.class_name}' created with total=False"
        suggestion = "Convert to class definition and use NotRequired for optional fields"

    node_text = context.node.text()

    issue: TotalFalseIssue = TotalFalseIssue(
        type=context.issue_type,
        issue_description=issue_description,
        suggestion=suggestion,
        line=start.line + 1,
        column=start.column,
        end_line=end.line + 1,
        end_column=end.column,
        class_name=context.class_name,
        text=node_text,
    )
    return issue


def _extract_class_name(class_node: sg.SgNode) -> str:
    """Extract class name from a class node."""
    name_match: sg.SgNode | None = class_node.get_match("NAME")
    if name_match:
        return name_match.text()

    name_node: sg.SgNode | None = class_node.field("name")
    if name_node:
        return name_node.text()

    class_text = class_node.text()
    match = re.match(r"class\s+(\w+)", class_text)
    if match:
        return match.group(1)

    return "UnknownClass"


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
    file_path: str,
    tool_name: str,
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput,
) -> set[str]:
    """Get existing total=False violations from old content to avoid duplicate warnings."""
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
                    old_violations = detect_total_false_violations(pre_edit_content)
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

                old_violations = detect_total_false_violations(pre_edit_content)
                for violation in old_violations:
                    existing_violations.add(_create_violation_key(violation))
            except Exception:
                pass

    return existing_violations


def _create_violation_key(violation: TotalFalseIssue) -> str:
    """Create a unique key for a violation to detect duplicates."""
    return f"{violation['class_name']}:{violation['line']}:{violation['type']}"


def _format_code_with_line_numbers(code: str, start_line: int) -> str:
    """Format code with line numbers."""
    lines = code.split("\n")
    formatted_lines = []
    for i, line in enumerate(lines):
        line_num = start_line + i
        formatted_lines.append(f"{line_num:6} │ {line}")
    return "\n".join(formatted_lines)


def _generate_suggested_fix(violation: TotalFalseIssue) -> str:
    """Generate suggested fix for TypedDict total=False violation."""
    if violation["type"] == "class_definition":
        lines = violation["text"].split("\n")
        result_lines = []

        for line in lines:
            if "total=False" in line:
                fixed_line = re.sub(r",\s*total\s*=\s*False", "", line)
                fixed_line = re.sub(r"\(\s*TypedDict\s*,\s*\)", "(TypedDict)", fixed_line)
                fixed_line = re.sub(r"\(\s*typing\.TypedDict\s*,\s*\)", "(typing.TypedDict)", fixed_line)
                result_lines.append(fixed_line)
            else:
                result_lines.append(line)

        return "\n".join(result_lines) + "\n    # Add NotRequired imports and mark optional fields"
    else:
        return f"""from typing import TypedDict, NotRequired

class {violation["class_name"]}(TypedDict):
    # Define required fields
    required_field: str
    # Use NotRequired for optional fields
    optional_field: NotRequired[int]"""


if __name__ == "__main__":
    main()
