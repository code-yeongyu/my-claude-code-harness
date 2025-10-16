#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "comment-parser>=1.2.4"
# ]
# ///

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, TypedDict, cast

from comment_parser import comment_parser

BDD_KEYWORDS: set[str] = {
    "given",
    "when",
    "then",
    "arrange",
    "act",
    "assert",
    "when & then",
    "when&then",
}
SKIP_EXTENSIONS: set[str] = {
    "json",
    "xml",
    "yaml",
    "yml",
    "md",
    "html",
    "css",
    "toml",
    "ini",
    "conf",
    "config",
    "sh",
}


def main() -> None:
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    try:
        input_raw: str = sys.stdin.read()
        if not input_raw:
            print(f"[{hook_filename}] Skipping: No input provided")
            sys.exit(0)

        data: PostToolUseInput = json.loads(input_raw)
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"[{hook_filename}] Skipping: Invalid input format")
        sys.exit(0)

    tool_name = data["tool_name"]
    tool_input = data["tool_input"]

    if not isinstance(tool_input, dict):
        print(f"[{hook_filename}] Skipping: Invalid tool input")
        sys.exit(0)

    file_path: str = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]

    if not file_path:
        print(f"[{hook_filename}] Skipping: No file path provided")
        sys.exit(0)

    if get_file_extension(file_path) in SKIP_EXTENSIONS:
        print(f"[{hook_filename}] Skipping: Non-code file")
        sys.exit(0)

    existing_comments: set[str] = set() if tool_name == "Write" else get_existing_comments_normalized(file_path)

    all_findings: list[tuple[str, int, str]] = []

    try:
        match tool_name:
            case "Write":
                write_input = cast(WriteToolInput, tool_input)
                findings = _process_write_tool(write_input, file_path, existing_comments)
                all_findings.extend(findings)

            case "Edit":
                edit_input = cast(EditToolInput, tool_input)
                findings = _process_edit_tool(edit_input, file_path)
                all_findings.extend(findings)

            case "MultiEdit":
                multi_input = cast(MultiEditToolInput, tool_input)
                findings = _process_multiedit_tool(multi_input, file_path)
                all_findings.extend(findings)

            case _:
                print(f"[{hook_filename}] Skipping: Unknown tool type")
                sys.exit(0)

    except Exception:
        print(f"[{hook_filename}] Skipping: Unexpected error occurred")
        sys.exit(0)

    seen: set[tuple[str, str, int]] = set()
    unique_findings: list[tuple[str, int, str]] = []

    for finding in all_findings:
        file_path, line, comment = finding
        key: tuple[str, str, int] = (file_path, normalize_comment(comment), line)
        if key not in seen:
            seen.add(key)
            unique_findings.append(finding)

    message: str = build_error_message(unique_findings)

    if not message.strip():
        print(f"[{hook_filename}] Success: No problematic comments/docstrings found")
        sys.exit(0)

    print(f"\n{message}", file=sys.stderr)
    sys.exit(2)


def _process_write_tool(
    tool_input: WriteToolInput, file_path: str, existing_comments: set[str]
) -> list[tuple[str, int, str]]:
    """Process Write tool input and return findings."""
    content = tool_input["content"]
    if not content:
        return []

    return check_comments_in_content(content, file_path, existing_comments)


def _process_edit_tool(tool_input: EditToolInput, file_path: str) -> list[tuple[str, int, str]]:
    """Process Edit tool input and return findings."""
    new_string = tool_input["new_string"]
    if not new_string:
        return []

    old_string = tool_input["old_string"]

    start_line: int = 1
    if new_string and Path(file_path).exists():
        try:
            with open(file_path, encoding="utf-8") as f:
                current_content = f.read()
            index = current_content.find(new_string)
            if index != -1:
                start_line = current_content[:index].count("\n") + 1
        except Exception:
            pass

    old_comments: set[str] = set()
    if old_string:
        old_comment_list = extract_comments_from_string(old_string, file_path)
        for comment in old_comment_list:
            text = get_comment_text(comment)
            if text:
                old_comments.add(normalize_comment(text))

        old_docstring_list = extract_docstrings_from_string(old_string, file_path)
        for _, _, docstring_text in old_docstring_list:
            if docstring_text:
                old_comments.add(normalize_comment(docstring_text))

    return check_comments_in_content(new_string, file_path, old_comments, start_line)


def _process_multiedit_tool(tool_input: MultiEditToolInput, file_path: str) -> list[tuple[str, int, str]]:
    """Process MultiEdit tool input and return findings."""
    all_old_comments: set[str] = set()
    edits = tool_input["edits"]

    for edit in edits:
        if not isinstance(edit, dict):
            continue

        old_string = edit["old_string"]
        if old_string:
            old_comment_list = extract_comments_from_string(old_string, file_path)
            for comment in old_comment_list:
                text = get_comment_text(comment)
                if text:
                    all_old_comments.add(normalize_comment(text))

            old_docstring_list = extract_docstrings_from_string(old_string, file_path)
            for _, _, docstring_text in old_docstring_list:
                if docstring_text:
                    all_old_comments.add(normalize_comment(docstring_text))

    all_new_comments: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            continue

        new_string = edit["new_string"]
        if new_string:
            new_comment_list = extract_comments_from_string(new_string, file_path)
            for comment in new_comment_list:
                text = get_comment_text(comment)
                if text:
                    all_new_comments.add(normalize_comment(text))

            new_docstring_list = extract_docstrings_from_string(new_string, file_path)
            for _, _, docstring_text in new_docstring_list:
                if docstring_text:
                    all_new_comments.add(normalize_comment(docstring_text))

    comments_to_check = all_new_comments - all_old_comments

    if not comments_to_check:
        return []

    try:
        if Path(file_path).exists():
            with open(file_path, encoding="utf-8") as f:
                file_content = f.read()
        else:
            return []
    except Exception:
        return []

    all_comments = extract_comments_from_string(file_content, file_path)
    all_docstrings = extract_docstrings_from_string(file_content, file_path)
    all_findings: list[tuple[str, int, str]] = []

    for comment in all_comments:
        text = get_comment_text(comment)
        if not text:
            continue

        normalized = normalize_comment(text)

        if normalized not in comments_to_check:
            continue

        if is_shebang_comment(text):
            continue

        if is_bdd_comment(text):
            continue

        ext = get_file_extension(file_path)
        if ext == "py" and is_python_type_comment(text):
            continue

        line = get_comment_line(comment)
        all_findings.append((file_path, line, text.strip()))

    for _, line_no, docstring_text in all_docstrings:
        if not docstring_text:
            continue

        normalized_doc = normalize_comment(docstring_text)

        if normalized_doc not in comments_to_check:
            continue

        all_findings.append((file_path, line_no, docstring_text.strip()))

    return all_findings


def build_error_message(findings: list[tuple[str, int, str]]) -> str:
    """Build the standard error message for detected comments and docstrings."""
    if not findings:
        return ""

    cwd = Path.cwd()

    file_path = findings[0][0] if findings else ""
    try:
        path_obj = Path(file_path).resolve()
        if path_obj.is_relative_to(cwd):
            display_path = str(path_obj.relative_to(cwd))
        else:
            display_path = file_path
    except (ValueError, OSError):
        display_path = file_path

    comments_xml = f'<comments file="{display_path}">\n'
    for _, line, comment in findings:
        comments_xml += f'\t<comment line-number="{line}">{comment}</comment>\n'
    comments_xml += "</comments>"

    msg = "COMMENT/DOCSTRING DETECTED - IMMEDIATE ACTION REQUIRED\n\n"
    msg += "Your recent changes contain comments or docstrings, which triggered this hook.\n"
    msg += "You need to take immediate action. You must follow the conditions below.\n"
    msg += "(Listed in priority order - you must always act according to this priority order)\n\n"
    msg += "CRITICAL WARNING: This hook message MUST NEVER be ignored, even if you receive it multiple times.\n"
    msg += "You MUST provide corresponding explanation or action for EACH occurrence of this message.\n"
    msg += "Ignoring this message or failing to respond appropriately is strictly prohibited.\n\n"
    msg += "PRIORITY-BASED ACTION GUIDELINES:\n\n"
    msg += "1. This is a comment/docstring that already existed before\n"
    msg += "\t-> Explain to the user that this is an existing comment/docstring and proceed (justify it)\n\n"
    msg += "2. This is a newly written comment: but it's in given, when, then format\n"
    msg += "\t-> Tell the user it's a BDD comment and proceed (justify it)\n"
    msg += "\t-> Note: This applies to comments only, not docstrings\n\n"
    msg += "3. This is a newly written comment/docstring: but it's a necessary comment/docstring\n"
    msg += "\t-> Tell the user why this comment/docstring is absolutely necessary and proceed (justify it)\n"
    msg += (
        "\t-> Examples of necessary comments: complex algorithms, security-related, "
        "performance optimization, regex, mathematical formulas\n"
    )
    msg += "\t-> Examples of necessary docstrings: public API documentation, complex module/class interfaces\n"
    msg += "\t-> IMPORTANT: Most docstrings are unnecessary if the code is self-explanatory. "
    msg += "Only keep truly essential ones.\n\n"
    msg += "4. This is a newly written comment/docstring: but it's an unnecessary comment/docstring\n"
    msg += "\t-> Apologize to the user and remove the comment/docstring.\n"
    msg += "\t-> Make the code itself clearer so it can be understood without comments/docstrings.\n"
    msg += (
        "\t-> For verbose docstrings: refactor code to be self-documenting instead of adding lengthy explanations.\n\n"
    )
    msg += "MANDATORY REQUIREMENT: You must acknowledge this hook message and take one of the above actions.\n"
    msg += "Review in the above priority order and take the corresponding action EVERY TIME this appears.\n\n"
    msg += "Detected comments/docstrings:\n"
    msg += comments_xml

    return msg


def check_comments_in_content(
    content: str, file_path: str, existing_comments: set[str], base_line: int = 1
) -> list[tuple[str, int, str]]:
    """
    Check for problematic comments and docstrings in content.
    Returns list of (file_path, line_number, comment_text) tuples.
    """
    findings: list[tuple[str, int, str]] = []
    ext: str = get_file_extension(file_path)

    if ext in SKIP_EXTENSIONS:
        return findings

    if ext != "py":
        return findings

    comments: list[Any] = extract_comments_from_string(content, file_path)

    for comment in comments:
        text: str = get_comment_text(comment)
        if not text:
            continue

        normalized: str = normalize_comment(text)

        if normalized in existing_comments:
            continue

        if is_shebang_comment(text):
            continue

        if is_bdd_comment(text):
            continue

        if ext == "py" and is_python_type_comment(text):
            continue

        line: int = get_comment_line(comment) + base_line - 1
        findings.append((file_path, line, text.strip()))

    docstrings: list[tuple[str, int, str]] = extract_docstrings_from_string(content, file_path)
    for _, line_no, docstring_text in docstrings:
        if not docstring_text:
            continue

        normalized_doc: str = normalize_comment(docstring_text)

        if normalized_doc in existing_comments:
            continue

        line: int = line_no + base_line - 1
        findings.append((file_path, line, docstring_text.strip()))

    return findings


def get_existing_comments_normalized(file_path: str) -> set[str]:
    """Get set of normalized existing comments and docstrings from file."""
    normalized: set[str] = set()

    comments: list[Any] = extract_comments_from_file(file_path)
    for comment in comments:
        text: str = get_comment_text(comment)
        if text:
            normalized.add(normalize_comment(text))

    docstrings: list[tuple[str, int, str]] = extract_docstrings_from_file(file_path)
    for _, _, docstring_text in docstrings:
        if docstring_text:
            normalized.add(normalize_comment(docstring_text))

    return normalized


def extract_comments_from_file(file_path: str) -> list[Any]:
    """Extract comments from existing file."""
    if not file_path or not Path(file_path).exists():
        return []

    try:
        with open(file_path, encoding="utf-8") as f:
            content: str = f.read()
        return extract_comments_from_string(content, file_path)
    except Exception:
        return []


def extract_comments_from_string(content: str, file_path: str) -> list[Any]:
    """Extract comments from Python files only."""
    if not content:
        return []

    ext: str = get_file_extension(file_path)

    if ext != "py":
        return []

    try:
        comments = comment_parser.extract_comments_from_str(content, mime="text/x-python")
        return comments if comments else []
    except Exception:
        return []


def extract_docstrings_from_string(content: str, file_path: str) -> list[tuple[str, int, str]]:
    """
    Extract docstrings from Python code.
    Returns list of (file_path, line_number, docstring_text) tuples.
    """
    findings: list[tuple[str, int, str]] = []

    ext: str = get_file_extension(file_path)

    if ext != "py":
        return findings

    if not content:
        return findings

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return findings

    module_doc = ast.get_docstring(tree)
    if module_doc:
        if tree.body and isinstance(tree.body[0], ast.Expr):
            if isinstance(tree.body[0].value, ast.Constant):
                line_no = tree.body[0].lineno
                findings.append((file_path, line_no, module_doc))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node)
            if class_doc:
                if node.body and isinstance(node.body[0], ast.Expr):
                    if isinstance(node.body[0].value, ast.Constant):
                        line_no = node.body[0].lineno
                        findings.append((file_path, line_no, class_doc))

        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func_doc = ast.get_docstring(node)
            if func_doc:
                if node.body and isinstance(node.body[0], ast.Expr):
                    if isinstance(node.body[0].value, ast.Constant):
                        line_no = node.body[0].lineno
                        findings.append((file_path, line_no, func_doc))

    return findings


def extract_docstrings_from_file(file_path: str) -> list[tuple[str, int, str]]:
    """Extract docstrings from existing file."""
    if not file_path or not Path(file_path).exists():
        return []

    try:
        with open(file_path, encoding="utf-8") as f:
            content: str = f.read()
        return extract_docstrings_from_string(content, file_path)
    except Exception:
        return []


def get_comment_text(comment: object) -> str:
    """Safely extract text from comment object."""
    try:
        if hasattr(comment, "text"):
            text_attr = comment.text  # type: ignore[attr-defined]
            if callable(text_attr):
                return str(text_attr())
            return str(text_attr)
        return ""
    except Exception:
        return ""


def get_comment_line(comment: object) -> int:
    """Safely extract line number from comment object."""
    try:
        if hasattr(comment, "line_number"):
            line_attr = comment.line_number  # type: ignore[attr-defined]
            if callable(line_attr):
                result = line_attr()
                if result is not None:
                    return int(result)  # type: ignore[arg-type]
                return 1
            if line_attr is not None:
                return int(line_attr)  # type: ignore[arg-type]
            return 1
        return 1
    except Exception:
        return 1


def normalize_comment(text: str) -> str:
    """Normalize comment text for comparison."""
    return text.strip().lower()


def is_shebang_comment(comment_text: str) -> bool:
    """Check if comment is a shebang line (for any script language)."""
    stripped: str = comment_text.strip()
    return stripped.startswith("!/")


def is_bdd_comment(comment_text: str) -> bool:
    """Check if comment is a BDD keyword."""
    stripped: str = comment_text.strip().lower()
    return stripped in BDD_KEYWORDS


def is_python_type_comment(comment_text: str) -> bool:
    """Check if comment is a Python type checker directive."""
    stripped: str = comment_text.strip().lower()

    type_checker_prefixes = [
        "type:",
        "noqa",
        "pyright:",
        "ruff:",
        "mypy:",
        "pylint:",
        "flake8:",
        "pyre:",
        "pytype:",
    ]

    for prefix in type_checker_prefixes:
        if stripped.startswith(prefix):
            return True

    return False


def get_file_extension(file_path: str) -> str:
    """Get lowercase file extension without dot."""
    return Path(file_path).suffix.lower().lstrip(".")


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


ToolInputUnion = WriteToolInput | EditToolInput | MultiEditToolInput


if __name__ == "__main__":
    main()
