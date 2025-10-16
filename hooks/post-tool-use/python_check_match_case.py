#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ast-grep-py>=0.24.1",
# ]
# ///
"""
Match-case detector hook for Claude Code (PostToolUse version).
Detects if-elif-else chains that can be converted to match-case statements.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from re import Pattern
from typing import (
    Any,
    NoReturn,
    TypedDict,
)

import ast_grep_py as sg

TYPE_IGNORE_PATTERN: Pattern[str] = re.compile(r"#\s*type:\s*ignore(?:\[[\w,\s]+\])?(?:\s|$)")

EXIT_CODE_BLOCK_TOOL: int = 2
DEFAULT_TEXT_TRUNCATION: int = 80
LONG_TEXT_TRUNCATION: int = 120
TAB: str = "\t"


# Claude Code Hook TypedDicts (순서 유지)
class EditOperation(TypedDict):
    old_string: str
    new_string: str


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: ClaudeCodeToolInput
    tool_response: dict[str, Any]


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


# Type aliases
ClaudeCodeToolInput = WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput


# Custom TypedDicts for match-case detection
class ConditionInfo(TypedDict):
    variable: str
    operator: str
    value: str
    body: str


class ChainInfo(TypedDict):
    conditions: list[ConditionInfo]
    else_body: str | None
    start_line: int
    end_line: int
    original: str
    indent: int


class MatchCaseCandidate(TypedDict):
    start_line: int
    end_line: int
    variable: str
    conditions: list[ConditionInfo]
    else_body: str | None
    original_code: str
    suggested_fix: str


class MatchCaseContext:
    """Context for creating match-case candidates."""

    def __init__(
        self,
        if_node: sg.SgNode,
        chain: ChainInfo,
        variable: str,
    ) -> None:
        self.if_node: sg.SgNode = if_node
        self.chain: ChainInfo = chain
        self.variable: str = variable


def main() -> None:
    """Main entry point for the PostToolUse hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception:
        _handle_hook_error()


def detect_convertible_if_chains(code: str) -> list[MatchCaseCandidate]:
    """Detect if-elif-else chains that can be converted to match-case statements."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    node: sg.SgNode = root.root()

    candidates: list[MatchCaseCandidate] = []

    if_statements: list[sg.SgNode] = node.find_all(pattern="if $COND: $BODY")

    for if_stmt in if_statements:
        if _is_main_guard(if_stmt):
            continue

        candidate = _analyze_if_chain(if_stmt)
        if candidate:
            candidates.append(candidate)

    return candidates


def build_warning_message(violations: list[MatchCaseCandidate], file_path: str) -> str:
    """Build warning message for match-case conversion opportunities."""
    if not violations:
        return ""

    display_path: str = _get_display_path(file_path)

    msg = f"""MATCH-CASE CONVERSION OPPORTUNITY DETECTED

File: {display_path}
Found {len(violations)} if-elif-else chain{"s" if len(violations) > 1 else ""} that should be converted to match-case.

Python 3.10+ introduces match-case statements which provide cleaner, more readable pattern matching.
Your if-elif-else chains testing the same variable against different values should use match-case instead.

Detected violations:
"""

    for i, candidate in enumerate(violations, 1):
        msg += f"\n[{i}] Lines {candidate['start_line']}-{candidate['end_line']}: "
        msg += f"Variable '{candidate['variable']}' compared against {len(candidate['conditions'])} values\n"

        msg += "\nCurrent code:\n```python\n"
        msg += _format_code_with_line_numbers(candidate["original_code"], candidate["start_line"])
        msg += "\n```\n"

        msg += "\nSuggested fix:\n```python\n"
        msg += _format_code_with_line_numbers(candidate["suggested_fix"], candidate["start_line"])
        msg += "\n```\n"

    msg += "\nFIX IMMEDIATELY: Convert all if-elif-else chains to match-case statements for better readability."
    msg += "\nThis is the modern Python way and follows best practices for Python 3.10+."

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
        print("[check-match-case] Skipping: No input provided")
        sys.exit(0)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, KeyError, TypeError):
        print("[check-match-case] Skipping: Invalid input format")
        sys.exit(0)


def should_process(data: PostToolUseInput) -> bool:
    """Determine if the input should be processed."""
    tool_input = data["tool_input"]  # type: ignore[assignment]
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

    if not _check_python_version_compatible():
        return False

    excluded: bool = _is_excluded_path(file_path)
    return not excluded


def process_tool_input(data: PostToolUseInput) -> list[MatchCaseCandidate]:
    """Process tool input and detect violations in newly added content."""
    tool_name = data["tool_name"]
    tool_input = data["tool_input"]

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not isinstance(tool_input, dict):
        return []

    new_violations: list[MatchCaseCandidate] = []
    existing_violations: set[str] = _get_existing_violations(file_path, tool_name, tool_input)

    match tool_name:
        case "Write" if isinstance(tool_input, dict):
            all_violations = _process_write_tool(tool_input)  # type: ignore[arg-type]
        case "Edit" if isinstance(tool_input, dict):
            all_violations = _process_edit_tool(tool_input)  # type: ignore[arg-type]
        case "MultiEdit" if isinstance(tool_input, dict):
            all_violations = _process_multiedit_tool(tool_input)  # type: ignore[arg-type]
        case _:
            all_violations = []

    for violation in all_violations:
        violation_key = _create_violation_key(violation)
        if violation_key not in existing_violations:
            new_violations.append(violation)

    return new_violations


def handle_findings(violations: list[MatchCaseCandidate], file_path: str) -> NoReturn:
    """Handle detected violations and provide feedback to Claude."""
    if not violations:
        print("[check-match-case] Success: No convertible if-elif-else chains found")
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
        print("[check-match-case] Skipping: File not eligible for processing")
        sys.exit(0)
    violations: list[MatchCaseCandidate] = process_tool_input(data)
    file_path: str = extract_file_path(data)
    handle_findings(violations, file_path)


def _handle_hook_error() -> NoReturn:
    """Handle errors in hook execution."""
    print("[check-match-case] Skipping: Unexpected error occurred")
    sys.exit(0)


def _is_excluded_path(file_path: str) -> bool:
    """Check if file path should be excluded from processing."""
    if "/hooks/" in file_path:
        return True

    if (
        "/test/" in file_path
        or "/tests/" in file_path
        or file_path.endswith("_test.py")
        or "test_" in Path(file_path).name
    ):
        return True

    return False


def _check_python_version_compatible() -> bool:
    """Check if Python version is 3.10 or higher."""
    if sys.version_info.major == 3 and sys.version_info.minor >= 10:
        return True

    pyproject_path = Path("pyproject.toml")
    if pyproject_path.exists():
        try:
            with open(pyproject_path) as f:
                content = f.read()

            match = re.search(r'requires-python\s*=\s*"[^"]*3\.(\d+)', content)
            if match:
                minor = int(match.group(1))
                return minor >= 10

            match = re.search(r'target-version\s*=\s*"py3(\d+)"', content)
            if match:
                minor = int(match.group(1))
                return minor >= 10
        except Exception:
            pass

    return False


def _process_write_tool(tool_input: WriteToolInput) -> list[MatchCaseCandidate]:
    """Process Write tool - all content is new."""
    content = tool_input["content"]
    if not content:
        return []
    return detect_convertible_if_chains(content)


def _process_edit_tool(tool_input: EditToolInput) -> list[MatchCaseCandidate]:
    """Process Edit tool - check only new content."""
    new_string = tool_input["new_string"]
    old_string = tool_input["old_string"]

    if not new_string:
        return []

    new_candidates = detect_convertible_if_chains(new_string)

    if old_string:
        old_candidates = detect_convertible_if_chains(old_string)
        old_keys = {_create_violation_key(c) for c in old_candidates}
        new_candidates = [c for c in new_candidates if _create_violation_key(c) not in old_keys]

    return new_candidates


def _process_multiedit_tool(tool_input: MultiEditToolInput) -> list[MatchCaseCandidate]:
    """Process MultiEdit tool - check all new content."""
    all_candidates: list[MatchCaseCandidate] = []

    edits = tool_input["edits"]
    for edit in edits:
        if isinstance(edit, dict):
            new_string = edit.get("new_string", "")
            old_string = edit.get("old_string", "")

            if new_string:
                new_candidates = detect_convertible_if_chains(new_string)

                if old_string:
                    old_candidates = detect_convertible_if_chains(old_string)
                    old_keys = {_create_violation_key(c) for c in old_candidates}
                    new_candidates = [c for c in new_candidates if _create_violation_key(c) not in old_keys]

                all_candidates.extend(new_candidates)

    return all_candidates


def _is_main_guard(if_node: sg.SgNode) -> bool:
    """Check if this is if __name__ == "__main__": pattern."""
    text: str = if_node.text()
    return "__name__" in text and ('"__main__"' in text or "'__main__'" in text)


def _analyze_if_chain(if_node: sg.SgNode) -> MatchCaseCandidate | None:
    """Analyze an if statement to see if it's a convertible chain."""
    chain = _extract_if_elif_chain(if_node)
    if not chain:
        return None

    variable = _extract_common_variable(chain["conditions"])
    if not variable:
        return None

    if not _is_simple_comparison_chain(chain["conditions"]):
        return None

    fix = _generate_match_case_fix(variable, chain["conditions"], chain.get("else_body"), chain["indent"])

    return MatchCaseCandidate(
        start_line=chain["start_line"],
        end_line=chain["end_line"],
        variable=variable,
        conditions=chain["conditions"],
        else_body=chain.get("else_body"),
        original_code=if_node.text(),
        suggested_fix=fix,
    )


def _extract_if_elif_chain(if_node: sg.SgNode) -> ChainInfo | None:
    """Extract complete if-elif-else chain structure."""
    text: str = if_node.text()
    lines: list[str] = text.split("\n")

    node_range = if_node.range()
    start = node_range.start
    end = node_range.end

    conditions: list[ConditionInfo] = []
    else_body: str | None = None
    current_condition: str | None = None
    current_variable: str | None = None
    current_operator: str | None = None
    current_value: str | None = None
    current_body_lines: list[str] = []
    in_body = False
    base_indent = 0

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if i == 0:
            base_indent = indent

        if stripped.startswith("if ") and i == 0:
            condition = stripped[3:].rstrip(":")
            parsed = _parse_condition(condition)
            if parsed:
                current_variable = parsed[0]
                current_operator = parsed[1]
                current_value = parsed[2]
                in_body = True
                current_body_lines = []
                current_condition = condition

        elif stripped.startswith("elif "):
            if current_condition and current_body_lines and current_variable and current_operator and current_value:
                body = "\n".join(current_body_lines)
                conditions.append(
                    ConditionInfo(
                        variable=current_variable,
                        operator=current_operator,
                        value=current_value,
                        body=body,
                    )
                )

            condition = stripped[5:].rstrip(":")
            parsed = _parse_condition(condition)
            if parsed:
                current_variable = parsed[0]
                current_operator = parsed[1]
                current_value = parsed[2]
                in_body = True
                current_body_lines = []
                current_condition = condition
            else:
                return None

        elif stripped.startswith("else:"):
            if current_condition and current_body_lines and current_variable and current_operator and current_value:
                body = "\n".join(current_body_lines)
                conditions.append(
                    ConditionInfo(
                        variable=current_variable,
                        operator=current_operator,
                        value=current_value,
                        body=body,
                    )
                )

            current_condition = None
            in_body = True
            current_body_lines = []

        elif in_body and indent > base_indent:
            current_body_lines.append(line)

    if current_condition and current_body_lines and current_variable and current_operator and current_value:
        body = "\n".join(current_body_lines)
        conditions.append(
            ConditionInfo(
                variable=current_variable,
                operator=current_operator,
                value=current_value,
                body=body,
            )
        )
    elif current_body_lines and not current_condition:
        else_body = "\n".join(current_body_lines)

    if not conditions:
        return None

    if len(conditions) < 2:
        return None

    return ChainInfo(
        conditions=conditions,
        else_body=else_body,
        start_line=start.line + 1,
        end_line=end.line + 1,
        original=text,
        indent=base_indent,
    )


def _parse_condition(condition: str) -> tuple[str, str, str] | None:
    """Parse a condition to extract variable, operator, value."""
    eq_match = re.match(r"^\s*([\w\.]+)\s*==\s*(.+)$", condition)
    if eq_match:
        return (eq_match.group(1).strip(), "==", eq_match.group(2).strip())

    isinstance_match = re.match(r"^\s*isinstance\s*\(\s*([\w\.]+)\s*,\s*(.+)\s*\)\s*$", condition)
    if isinstance_match:
        variable = isinstance_match.group(1).strip()
        types_str = isinstance_match.group(2).strip()

        if types_str.startswith("(") and types_str.endswith(")"):
            types_str = types_str[1:-1]

        return (variable, "isinstance", types_str)

    return None


def _extract_common_variable(conditions: list[ConditionInfo]) -> str | None:
    """Extract the common variable from all conditions."""
    if not conditions:
        return None

    variables = set()
    for cond in conditions:
        if "variable" in cond:
            variables.add(cond["variable"])

    if len(variables) == 1:
        return variables.pop()
    return None


def _is_simple_comparison_chain(conditions: list[ConditionInfo]) -> bool:
    """Check if all conditions are simple comparisons (== or isinstance)."""
    for cond in conditions:
        if cond["operator"] not in ["==", "isinstance"]:
            return False

    operators = {cond["operator"] for cond in conditions}
    if len(operators) > 1:
        return False

    return True


def _generate_match_case_fix(
    variable: str, conditions: list[ConditionInfo], else_body: str | None, base_indent: int
) -> str:
    """Generate match-case statement from conditions."""
    indent = " " * base_indent
    lines = [f"{indent}match {variable}:"]

    for cond in conditions:
        value = cond["value"]
        body = cond["body"]
        operator = cond["operator"]

        if operator == "isinstance":
            case_pattern = _format_isinstance_pattern(value, variable, body)
        else:
            case_pattern = value

        lines.append(f"{indent}    case {case_pattern}:")
        for body_line in body.split("\n"):
            if body_line.strip():
                lines.append(body_line)

    if else_body:
        lines.append(f"{indent}    case _:")
        for body_line in else_body.split("\n"):
            if body_line.strip():
                lines.append(body_line)

    return "\n".join(lines)


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


def _format_code_with_line_numbers(code: str, start_line: int) -> str:
    """Format code with line numbers."""
    lines = code.split("\n")
    formatted_lines = []
    for i, line in enumerate(lines):
        line_num = start_line + i
        formatted_lines.append(f"{line_num:6} │ {line}")
    return "\n".join(formatted_lines)


def _get_existing_violations(file_path: str, tool_name: str, tool_input: ClaudeCodeToolInput) -> set[str]:
    """Get existing violations from old content to avoid duplicate warnings."""
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
                    old_violations = detect_convertible_if_chains(pre_edit_content)
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

                old_violations = detect_convertible_if_chains(pre_edit_content)
                for violation in old_violations:
                    existing_violations.add(_create_violation_key(violation))
            except Exception:
                pass

    return existing_violations


def _format_isinstance_pattern(types_str: str, variable: str, body: str) -> str:
    """Format isinstance type(s) into match-case pattern."""
    type_names = re.findall(r"[A-Za-z_][\w\.]*", types_str)

    if not type_names:
        return "_"

    if len(type_names) == 1:
        pattern = f"{type_names[0]}()"
    else:
        patterns = [f"{name}()" for name in type_names]
        pattern = " | ".join(patterns)

    if _variable_used_in_body(variable, body):
        pattern += f" as {variable}"

    return pattern


def _variable_used_in_body(variable: str, body: str) -> bool:
    """Check if variable is used in the body."""
    pattern = rf"\b{re.escape(variable)}\b"
    return bool(re.search(pattern, body))


def _create_violation_key(candidate: MatchCaseCandidate) -> str:
    """Create a unique key for a candidate to detect duplicates."""
    values = [cond["value"] for cond in candidate["conditions"]]
    return f"{candidate['variable']}:{','.join(values)}"


if __name__ == "__main__":
    main()
