#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ast-grep-py>=0.24.1",
# ]
# ///
"""
Nested import detector hook for Claude Code (PostToolUse version).
Detects nested imports after file modifications and provides feedback to Claude for correction.
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


class NestedImportIssue(TypedDict):
    type: Literal["import", "from_import"]
    issue_description: str
    suggestion: str
    line: int
    column: int
    end_line: int
    end_column: int
    function_name: str
    module_name: str
    text: str
    is_type_checking: bool
    from_items: str | None


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


class ImportContext:
    """Context for creating nested import issues."""

    def __init__(
        self,
        import_type: Literal["import", "from_import"],
        import_node: sg.SgNode,
        function_name: str,
        module_name: str,
        is_type_checking: bool,
        from_items: str | None,
    ) -> None:
        self.import_type: Literal["import", "from_import"] = import_type
        self.import_node: sg.SgNode = import_node
        self.function_name: str = function_name
        self.module_name: str = module_name
        self.is_type_checking: bool = is_type_checking
        self.from_items: str | None = from_items


def run_nested_import_check() -> None:
    """Main entry point for the PostToolUse hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception:
        _handle_hook_error()


def detect_nested_import_violations(code: str) -> list[NestedImportIssue]:
    """Detect all nested import violations in the given code."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    node: sg.SgNode = root.root()
    source_lines: list[str] = code.split("\n")

    violations: list[NestedImportIssue] = []

    import_violations: list[NestedImportIssue] = _detect_import_statements(node, source_lines)
    violations.extend(import_violations)

    from_import_violations: list[NestedImportIssue] = _detect_from_import_statements(node, source_lines)
    violations.extend(from_import_violations)

    filtered_violations: list[NestedImportIssue] = [
        violation for violation in violations if not violation["is_type_checking"]
    ]
    return filtered_violations


def build_warning_message(violations: list[NestedImportIssue], file_path: str) -> str:
    """Build warning message for nested import violations."""
    if not violations:
        return ""

    display_path: str = _get_display_path(file_path)

    msg = f"""NESTED IMPORT DETECTED - CRITICAL WARNING

File: {display_path}
Found {len(violations)} nested import{"s" if len(violations) > 1 else ""}.

Nested imports violate PEP 8 and your project's import rules.
All imports must be at the top of the file.

Detected violations:
"""

    for i, violation in enumerate(violations, 1):
        msg += f"\n[{i}] Line {violation['line']}: {violation['issue_description']}\n"
        msg += f"\t{violation['suggestion']}\n"

    msg += "\nFIX IMMEDIATELY: Move all imports to the top of the file."

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


def adjust_line_numbers_for_edit(
    violations: list[NestedImportIssue],
    file_path: str,
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput,
) -> list[NestedImportIssue]:
    """Adjust line numbers for Edit tool based on position in file."""
    if not violations or not file_path or not Path(file_path).exists():
        return violations

    try:
        with open(file_path, encoding="utf-8") as file_handle:
            full_content: str = file_handle.read()

        old_string = tool_input["old_string"]  # type: ignore[literal-required]
        new_string = tool_input["new_string"]  # type: ignore[literal-required]

        index: int = full_content.find(old_string) if old_string else full_content.find(new_string)

        if index != -1:
            lines_before: int = full_content[:index].count("\n")
            violation: NestedImportIssue
            for violation in violations:
                violation["line"] += lines_before
    except Exception:
        pass

    return violations


def parse_input() -> PostToolUseInput:
    """Parse and validate stdin input."""
    input_raw: str = sys.stdin.read()
    if not input_raw:
        print("[check-nested-imports] Skipping: No input provided")
        sys.exit(0)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, KeyError, TypeError):
        print("[check-nested-imports] Skipping: Invalid input format")
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


def process_tool_input(data: PostToolUseInput) -> list[NestedImportIssue]:
    """Process tool input and detect violations in newly added content."""
    tool_name = data["tool_name"]
    tool_input = data["tool_input"]

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not isinstance(tool_input, dict):
        return []

    new_violations: list[NestedImportIssue] = []
    existing_violations: set[str] = _get_existing_violations(file_path, tool_name, tool_input)  # type: ignore[arg-type]

    match tool_name:
        case "Write":
            all_violations = _process_write_tool_post(tool_input)
        case "Edit":
            all_violations = _process_edit_tool_post(tool_input)
        case "MultiEdit":
            all_violations = _process_multiedit_tool_post(tool_input)
        case _:
            all_violations = []

    for violation in all_violations:
        violation_key = _create_violation_key(violation)
        if violation_key not in existing_violations:
            new_violations.append(violation)

    return new_violations


def handle_findings(violations: list[NestedImportIssue], file_path: str) -> NoReturn:
    """Handle detected violations and provide feedback to Claude."""
    if not violations:
        print("[check-nested-imports] Success: No nested import violations found")
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
        print("[check-nested-imports] Skipping: File not eligible for processing")
        sys.exit(0)
    violations: list[NestedImportIssue] = process_tool_input(data)
    file_path: str = extract_file_path(data)
    handle_findings(violations, file_path)


def _handle_hook_error() -> NoReturn:
    """Handle errors in hook execution."""
    print("[check-nested-imports] Skipping: Unexpected error occurred")
    sys.exit(0)


def _is_excluded_path(file_path: str) -> bool:
    """Check if file path should be excluded from processing."""
    return False


def _process_write_tool_post(
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput,
) -> list[NestedImportIssue]:
    """Process Write tool input for PostToolUse - read from actual file."""
    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]
    content = _read_file_content(file_path)
    if content is None:
        return []
    return detect_nested_import_violations(content) if content else []


def _process_edit_tool_post(
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput,
) -> list[NestedImportIssue]:
    """Process Edit tool input for PostToolUse - check actual file after edit."""
    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]
    content = _read_file_content(file_path)
    if content is None:
        return []
    return detect_nested_import_violations(content)


def _process_multiedit_tool_post(
    tool_input: WriteToolInput | EditToolInput | MultiEditToolInput,
) -> list[NestedImportIssue]:
    """Process MultiEdit tool input for PostToolUse - check actual file after edits."""
    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]
    content = _read_file_content(file_path)
    if content is None:
        return []
    return detect_nested_import_violations(content)


def _detect_import_statements(node: sg.SgNode, source_lines: list[str]) -> list[NestedImportIssue]:
    """Detect regular import statements inside functions."""
    violations: list[NestedImportIssue] = []

    import_nodes: list[sg.SgNode] = node.find_all(pattern="import $MODULE")

    import_node: sg.SgNode
    for import_node in import_nodes:
        parent_function: sg.SgNode | None = _find_parent_function(import_node)
        if parent_function:
            context: ImportContext = ImportContext(
                import_type="import",
                import_node=import_node,
                function_name=_extract_function_name(parent_function),
                module_name=_extract_module_from_import(import_node),
                is_type_checking=_is_type_checking_import(import_node, source_lines),
                from_items=None,
            )
            issue: NestedImportIssue = _create_nested_import_issue(context)
            violations.append(issue)

    return violations


def _detect_from_import_statements(node: sg.SgNode, source_lines: list[str]) -> list[NestedImportIssue]:
    """Detect from...import statements inside functions."""
    violations: list[NestedImportIssue] = []

    from_import_nodes: list[sg.SgNode] = node.find_all(pattern="from $MODULE import $$$ITEMS")

    from_import_node: sg.SgNode
    for from_import_node in from_import_nodes:
        parent_function: sg.SgNode | None = _find_parent_function(from_import_node)
        if parent_function:
            context: ImportContext = ImportContext(
                import_type="from_import",
                import_node=from_import_node,
                function_name=_extract_function_name(parent_function),
                module_name=_extract_module_from_from_import(from_import_node),
                is_type_checking=_is_type_checking_import(from_import_node, source_lines),
                from_items=_extract_items_from_from_import(from_import_node),
            )
            issue: NestedImportIssue = _create_nested_import_issue(context)
            violations.append(issue)

    return violations


def _create_nested_import_issue(context: ImportContext) -> NestedImportIssue:
    """Create a NestedImportIssue object from context."""
    start: Any = context.import_node.range().start
    end: Any = context.import_node.range().end

    issue_description: str
    suggestion: str

    if context.import_type == "import":
        issue_description = f"Import '{context.module_name}' inside function '{context.function_name}'"
        suggestion = f"Move 'import {context.module_name}' to the top of the file"
    else:
        issue_description = f"Import from '{context.module_name}' inside function '{context.function_name}'"
        suggestion = f"Move 'from {context.module_name} import {context.from_items}' to the top of the file"

    issue: NestedImportIssue = NestedImportIssue(
        type=context.import_type,
        issue_description=issue_description,
        suggestion=suggestion,
        line=start.line + 1,
        column=start.column,
        end_line=end.line + 1,
        end_column=end.column,
        function_name=context.function_name,
        module_name=context.module_name,
        text=_truncate_text(context.import_node.text(), DEFAULT_TEXT_TRUNCATION),
        is_type_checking=context.is_type_checking,
        from_items=context.from_items,
    )
    return issue


def _find_parent_function(node: sg.SgNode) -> sg.SgNode | None:
    """Find the parent function of a node."""
    current: sg.SgNode | None = node.parent()
    while current:
        if current.kind() == "function_definition":
            return current
        current = current.parent()
    return None


def _extract_function_name(function_node: sg.SgNode) -> str:
    """Extract function name from a function node."""
    name_node: sg.SgNode | None = function_node.field("name")
    if name_node:
        return name_node.text()
    return "unknown"


def _extract_module_from_import(import_node: sg.SgNode) -> str:
    """Extract module name from import statement."""
    module_match: sg.SgNode | None = import_node.get_match("MODULE")
    if module_match:
        return module_match.text()
    return "unknown"


def _extract_module_from_from_import(from_import_node: sg.SgNode) -> str:
    """Extract module name from from...import statement."""
    module_match: sg.SgNode | None = from_import_node.get_match("MODULE")
    if module_match:
        return module_match.text()
    return "unknown"


def _extract_items_from_from_import(from_import_node: sg.SgNode) -> str:
    """Extract imported items from from...import statement."""
    items_matches: list[sg.SgNode] = from_import_node.get_multiple_matches("ITEMS")
    if items_matches:
        items_text: list[str] = [item.text() for item in items_matches]
        return "".join(items_text)
    return "unknown"


def _is_type_checking_import(import_node: sg.SgNode, source_lines: list[str]) -> bool:
    """Check if import is inside TYPE_CHECKING block or has # type: ignore comment."""
    import_line_num: int = import_node.range().start.line
    if import_line_num < len(source_lines):
        import_line: str = source_lines[import_line_num]
        if TYPE_IGNORE_PATTERN.search(import_line):
            return True

    current: sg.SgNode | None = import_node.parent()
    while current:
        if current.kind() == "if_statement":
            condition_node: sg.SgNode | None = current.field("condition")
            if condition_node:
                condition_text: str = condition_node.text()
                if "TYPE_CHECKING" in condition_text:
                    return True
        current = current.parent()

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
    """Get existing nested import violations from old content to avoid duplicate warnings."""
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
                    old_violations = detect_nested_import_violations(pre_edit_content)
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

                old_violations = detect_nested_import_violations(pre_edit_content)
                for violation in old_violations:
                    existing_violations.add(_create_violation_key(violation))
            except Exception:
                pass

    return existing_violations


def _create_violation_key(violation: NestedImportIssue) -> str:
    """Create a unique key for a violation to detect duplicates."""
    return f"{violation['module_name']}:{violation['function_name']}:{violation['type']}"


if __name__ == "__main__":
    run_nested_import_check()
