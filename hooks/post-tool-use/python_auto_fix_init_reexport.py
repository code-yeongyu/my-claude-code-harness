#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ast-grep-py>=0.24.1",
# ]
# ///
"""
Auto-fix hook for converting implicit imports to explicit re-exports in __init__.py.
Automatically converts 'from X import Y' to 'from X import Y as Y' for items in __all__.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, NoReturn, TypedDict

import ast_grep_py as sg  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]

EXIT_CODE_BLOCK_TOOL: int = 2


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


ClaudeCodeToolInput = WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput


def main() -> None:
    """Main entry point for the PostToolUse hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        _execute_hook_pipeline()
    except Exception:
        _handle_hook_error()


def parse_input() -> PostToolUseInput:
    """Parse and validate stdin input."""
    input_raw: str = sys.stdin.read()
    if not input_raw:
        print("[auto-fix-init-reexport] Skipping: No input provided")
        sys.exit(0)

    try:
        parsed_data: PostToolUseInput = json.loads(input_raw)
        return parsed_data
    except (json.JSONDecodeError, KeyError, TypeError):
        print("[auto-fix-init-reexport] Skipping: Invalid input format")
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

    if not file_path or not file_path.endswith("__init__.py"):
        return False

    excluded: bool = _is_excluded_path(file_path)
    return not excluded


def process_and_fix(data: PostToolUseInput) -> bool:
    """Process file and apply auto-fix if needed. Returns True if file was modified."""
    tool_input = data["tool_input"]

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not file_path or not Path(file_path).exists():
        return False

    try:
        with open(file_path, encoding="utf-8") as f:
            original_content = f.read()

        fixed_content = _auto_fix_implicit_imports(original_content)

        if fixed_content != original_content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(fixed_content)
            print(f"[auto-fix-init-reexport] ✓ Auto-fixed: {Path(file_path).name}", file=sys.stderr)
            return True

        return False
    except Exception:
        return False


def _execute_hook_pipeline() -> NoReturn:
    """Execute the main hook logic pipeline."""
    data: PostToolUseInput = parse_input()
    if not should_process(data):
        print("[auto-fix-init-reexport] Skipping: File not eligible for processing")
        sys.exit(0)

    modified = process_and_fix(data)
    if modified:
        tool_input = data["tool_input"]
        file_path: str = ""
        if "file_path" in tool_input:
            file_path = tool_input["file_path"]  # type: ignore[literal-required]

        display_path = _get_display_path(file_path) if file_path else "file"

        message = f"""AUTO-FIX APPLIED: Explicit Re-exports

File: {display_path}

Automatically converted implicit imports to explicit re-exports.
Changed: from X import Y  →  from X import Y as Y

This ensures proper type checking and IDE autocomplete functionality.
"""
        print(message, file=sys.stderr)
        sys.exit(EXIT_CODE_BLOCK_TOOL)

    sys.exit(0)


def _handle_hook_error() -> NoReturn:
    """Handle errors in hook execution."""
    print("[auto-fix-init-reexport] Skipping: Unexpected error occurred")
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


def _auto_fix_implicit_imports(code: str) -> str:
    """Auto-fix implicit imports to explicit re-exports."""
    all_items = _extract_all_items_from_code(code)
    if not all_items:
        return code

    explicit_reexports = _find_explicit_reexports(code)
    missing_items = [item for item in all_items if item not in explicit_reexports]

    fixed_code = code
    for item in missing_items:
        updated_code = _convert_import_to_explicit_reexport(fixed_code, item)
        fixed_code = updated_code

    updated_explicit_reexports = _find_explicit_reexports(fixed_code)
    if all(item in updated_explicit_reexports for item in all_items):
        fixed_code = _remove_all_assignment(fixed_code)

    return fixed_code


def _remove_all_assignment(code: str) -> str:
    """Remove the __all__ assignment when explicit re-exports are present."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    assignment: sg.SgNode | None = root.root().find(pattern="__all__ = $VALUE")
    if assignment is None:
        return code

    target = assignment.text()
    updated = code.replace(target, "", 1)
    while "\n\n\n" in updated:
        updated = updated.replace("\n\n\n", "\n\n")

    return updated


def _extract_all_items_from_code(code: str) -> list[str]:
    """Extract items from __all__ assignment using ast-grep."""
    root: sg.SgRoot = sg.SgRoot(code, "python")
    node: sg.SgNode = root.root()

    all_assignments: list[sg.SgNode] = node.find_all(pattern="__all__ = $VALUE")

    for assignment in all_assignments:
        text = assignment.text()
        items = _extract_items_from_all_text(text)
        if items:
            return items

    return []


def _extract_items_from_all_text(text: str) -> list[str]:
    """Extract items from __all__ text."""
    match = re.search(r"__all__\s*=\s*[\[\(]([^\]\)]+)[\]\)]", text, re.DOTALL)
    if not match:
        return []

    items_str = match.group(1)
    items = re.findall(r'["\']([^"\']+)["\']', items_str)
    return items


def _find_explicit_reexports(code: str) -> set[str]:
    """Find all explicit re-exports (from X import Y as Y) in the code."""
    explicit_reexports: set[str] = set()

    pattern = r"from\s+[\w\.]+\s+import\s+([\w\s,]+(?:\s+as\s+\w+)?(?:\s*,\s*\w+\s+as\s+\w+)*)"
    matches = re.findall(pattern, code)

    for match in matches:
        items = [item.strip() for item in match.split(",")]
        for item in items:
            if " as " in item:
                parts = item.split(" as ")
                if len(parts) == 2:
                    imported_name = parts[0].strip()
                    aliased_name = parts[1].strip()
                    if imported_name == aliased_name:
                        explicit_reexports.add(imported_name)

    return explicit_reexports


def _convert_import_to_explicit_reexport(code: str, item: str) -> str:
    """Convert a single import to explicit re-export format."""
    lines = code.split("\n")
    modified = False
    insert_position = -1

    for i, line in enumerate(lines):
        if modified:
            break

        if not line.strip().startswith("from "):
            continue

        match = re.match(r"^(\s*from\s+([\w\.]+)\s+import\s+)(.+)$", line)
        if not match:
            continue

        indent = match.group(1)
        match.group(2)
        imports_part = match.group(3)

        import_items = [x.strip() for x in imports_part.split(",")]
        remaining_items = []
        item_found = False

        for import_item in import_items:
            if " as " in import_item:
                remaining_items.append(import_item)
            elif import_item == item:
                item_found = True
                insert_position = i
            else:
                remaining_items.append(import_item)

        if item_found:
            if remaining_items:
                lines[i] = indent + ", ".join(remaining_items)
            else:
                lines[i] = ""

            new_line = f"{indent}{item} as {item}"
            lines.insert(insert_position, new_line)
            modified = True

    result = "\n".join(lines)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    return result


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


if __name__ == "__main__":
    main()
