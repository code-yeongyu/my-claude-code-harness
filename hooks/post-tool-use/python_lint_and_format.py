#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["toml>=0.10.2"]
# ///

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import toml

# Configuration constants
ALWAYS_ENFORCE_RULES: list[str] = [
    "ASYNC",
    "ANN001",
    "ANN201",
    "ANN202",
    "ANN204",
    "ANN205",
    "ANN206",
    "ANN401",
]
FALLBACK_LINT_RULES: list[str] = [
    "PLE",
    "PLW",
    "E",
    "W",
    "F",
    "I",
    "Q",
    "UP",
    "C4",
    "PT",
]
FALLBACK_EXCLUDE_PATHS: list[str] = []
FALLBACK_LINE_LENGTH: int = 119

VENV_RUFF_PATHS: list[Path] = [
    Path(".venv/bin/ruff"),
    Path("venv/bin/ruff"),
]
PYPROJECT_PATH = Path("pyproject.toml")

RUFF_CMD_CHECK = "check"
RUFF_CMD_FORMAT = "format"

RUFF_FLAG_UNSAFE_FIXES = "--unsafe-fixes"
RUFF_FLAG_FIX = "--fix"
RUFF_FLAG_EXIT_NON_ZERO_ON_FIX = "--exit-non-zero-on-fix"
RUFF_FLAG_EXTEND_SELECT = "--extend-select"
RUFF_FLAG_EXTEND_IGNORE = "--extend-ignore"
RUFF_FLAG_SELECT = "--select"
RUFF_FLAG_EXCLUDE = "--exclude"
RUFF_FLAG_LINE_LENGTH = "--line-length"
RUFF_FLAG_TARGET_VERSION = "--target-version"

# Compiled regex patterns
VERSION_WITH_OPERATOR_PATTERN: re.Pattern[str] = re.compile(r"[~>=<!=]+\s*(\d+\.\d+(?:\.\d+)?)")
VERSION_WITHOUT_OPERATOR_PATTERN: re.Pattern[str] = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


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


class EditToolInput(TypedDict):
    file_path: str
    old_string: str
    new_string: str


class NotebookEditToolInput(TypedDict):
    notebook_path: str
    new_source: str


class PyprojectToml(TypedDict):
    tool: NotRequired[ToolConfig]


class ToolConfig(TypedDict):
    ruff: NotRequired[RuffConfig]


class RuffConfig(TypedDict):
    lint: NotRequired[RuffLintConfig]


class RuffLintConfig(TypedDict):
    select: NotRequired[list[str]]
    extend_select: NotRequired[list[str]]
    ignore: NotRequired[list[str]]


@dataclass
class RuffConfiguration:
    executable_path: str
    lint_args: list[str]
    format_args: list[str]
    use_fallback: bool


@dataclass
class RuffResults:
    lint_output: str
    lint_exit_code: int
    format_output: str
    format_exit_code: int
    has_unused_imports: bool
    has_auto_fixes: bool


ClaudeCodeToolInput = WriteToolInput | EditToolInput | NotebookEditToolInput
PyprojectConfig = PyprojectToml | dict[str, Any]


def main() -> None:
    """Main entry point for the ruff hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    input_data = sys.stdin.read()
    file_path = get_target_file_path(input_data)
    if not file_path:
        print("[ruff-hook] Skipping: No valid Python file path provided or file is not .py")
        sys.exit(0)

    config = resolve_ruff_configuration()
    if not config:
        print("[ruff-hook] Skipping: ruff not found in .venv or system PATH")
        sys.exit(0)

    results = execute_ruff_operations(config, file_path)
    handle_results_and_exit(results)


def get_target_file_path(input_data: str) -> str | None:
    """Extract and validate the target Python file path from input.

    Returns:
        The file path if it's a valid Python file, None otherwise
    """
    if not input_data:
        return None

    try:
        data: PostToolUseInput = json.loads(input_data)
        tool_input = data["tool_input"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    file_path = ""
    if "file_path" in tool_input:
        file_path = tool_input["file_path"]  # type: ignore[literal-required]
    elif "notebook_path" in tool_input:
        file_path = tool_input["notebook_path"]  # type: ignore[literal-required]
    elif "target_file" in tool_input:  # Legacy support
        file_path = tool_input["target_file"]  # type: ignore[typeddict-item]

    return file_path if _is_valid_python_file(file_path) else None


def resolve_ruff_configuration() -> RuffConfiguration | None:
    """Resolve ruff executable path and configuration."""
    ruff_path, needs_fallback = find_ruff_executable()
    if not ruff_path:
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            return None
        needs_fallback = True

    if needs_fallback or should_use_fallback_config():
        lint_args, format_args = get_fallback_args()
        print_fallback_mode_info()
        use_fallback = True
    else:
        lint_args = [RUFF_FLAG_EXTEND_SELECT, ",".join(ALWAYS_ENFORCE_RULES)]
        format_args = []
        use_fallback = False

    return RuffConfiguration(
        executable_path=ruff_path,
        lint_args=lint_args,
        format_args=format_args,
        use_fallback=use_fallback,
    )


def execute_ruff_operations(config: RuffConfiguration, file_path: Path | str) -> RuffResults:
    """Execute all ruff operations and collect results.

    Args:
        config: Ruff configuration
        file_path: Path to the specific file to check
    """
    # Lint only
    GLOBAL_MODE = False
    if GLOBAL_MODE:
        file_path = "."
    output, exit_code = run_ruff_command(
        config.executable_path,
        [RUFF_CMD_CHECK, RUFF_FLAG_UNSAFE_FIXES] + config.lint_args + [str(file_path)],
    )
    lint_check_output = output.strip()
    has_unused_imports = exit_code != 0 and "F401" in lint_check_output

    # Lint & Fix (exclude F401 from auto-fix)
    fix_args = [
        RUFF_CMD_CHECK,
        RUFF_FLAG_FIX,
        RUFF_FLAG_EXIT_NON_ZERO_ON_FIX,
        RUFF_FLAG_UNSAFE_FIXES,
        RUFF_FLAG_EXTEND_IGNORE,
        "F401",
    ]
    fix_args.extend(config.lint_args)
    fix_args.append(str(file_path))
    fix_output, fix_exit_code = run_ruff_command(
        config.executable_path,
        fix_args,
    )
    fix_output = fix_output.strip()

    has_auto_fixes = fix_exit_code != 0

    if has_auto_fixes and bool(fix_output):
        fix_output, fix_exit_code = run_ruff_command(
            config.executable_path,
            [RUFF_CMD_CHECK, RUFF_FLAG_UNSAFE_FIXES] + config.lint_args + [str(file_path)],
        )
        fix_output = fix_output.strip()

    _, format_check_exit_code = run_ruff_command(
        config.executable_path,
        [RUFF_CMD_FORMAT, "--check"] + config.format_args + [str(file_path)],
    )

    has_format_changes = format_check_exit_code != 0

    if has_format_changes:
        run_ruff_command(
            config.executable_path,
            [RUFF_CMD_FORMAT] + config.format_args + [str(file_path)],
        )
        format_output = f"1 file reformatted: {file_path}"
    else:
        format_output = ""

    return RuffResults(
        lint_output=fix_output,
        lint_exit_code=fix_exit_code,
        format_output=format_output,
        format_exit_code=1 if has_format_changes else 0,
        has_unused_imports=has_unused_imports,
        has_auto_fixes=has_auto_fixes,
    )


def handle_results_and_exit(results: RuffResults) -> None:
    """Handle ruff results and exit with appropriate code."""
    has_only_formatting = (
        results.format_exit_code != 0 and results.lint_exit_code == 0 and not results.has_unused_imports
    )

    if has_only_formatting:
        if results.format_output:
            format_message = (
                results.format_output + "\n\n"
                "FILE REFORMATTED\n"
                "The file has been reformatted by ruff.\n"
                "NEXT STEP: Use Read() to view the reformatted content before making further edits."
            )
            print(
                f"\n{_wrap_in_xml_tags('ruff-format', format_message)}\n",
                file=sys.stderr,
            )
        sys.exit(2)

    message = _build_complete_error_message(results)

    if not message:
        print("[ruff-hook] Success: No lint or format issues found")
        sys.exit(0)

    if results.has_auto_fixes and results.lint_exit_code == 0 and results.format_exit_code == 0:
        print(message, file=sys.stderr)
        sys.exit(2)

    if results.lint_exit_code != 0 or results.format_exit_code != 0 or results.has_unused_imports:
        print(message, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


def find_ruff_executable() -> tuple[str | None, bool]:
    """Find ruff executable in .venv or system PATH.

    Returns:
        Tuple of (path_to_ruff, needs_fallback_config)
        If ruff is not found, returns (None, True)
    """
    if venv_ruff := next((p for p in VENV_RUFF_PATHS if p.exists()), None):
        return str(venv_ruff.absolute()), False

    system_ruff = shutil.which("ruff")
    if system_ruff:
        return system_ruff, False

    return None, True


def should_use_fallback_config() -> bool:
    """Check if fallback configuration should be used.

    Returns:
        True if fallback config should be used (no config file or no ruff config)
    """
    if not PYPROJECT_PATH.exists():
        return True

    try:
        with open(PYPROJECT_PATH) as f:
            config: PyprojectConfig = toml.load(f)

        has_ruff_config = "tool" in config and "ruff" in config["tool"]
        if not has_ruff_config:
            return True

        ruff_config = config["tool"]["ruff"]
        has_lint_select = "lint" in ruff_config and "select" in ruff_config["lint"]
        has_line_length = "line-length" in ruff_config

        return not (has_lint_select or has_line_length)
    except (OSError, toml.TomlDecodeError, KeyError, TypeError):
        return True


def get_python_version() -> str:
    """Get target Python version from pyproject.toml or current environment.

    Returns:
        Python version string (e.g., 'py311')
    """
    if not PYPROJECT_PATH.exists():
        return _get_current_python_version()

    try:
        with open(PYPROJECT_PATH) as f:
            config: PyprojectConfig = toml.load(f)

        target_version = _get_ruff_target_version(config)
        if target_version:
            return target_version

        project_version = _get_project_python_version(config)
        if project_version:
            return project_version

        return _get_current_python_version()
    except (OSError, toml.TomlDecodeError, KeyError, TypeError):
        return _get_current_python_version()


def get_fallback_args() -> tuple[list[str], list[str]]:
    """Get fallback ruff arguments for lint and format.

    Returns:
        Tuple of (lint_args, format_args)
    """
    all_rules = FALLBACK_LINT_RULES + ALWAYS_ENFORCE_RULES

    lint_args = [
        RUFF_FLAG_SELECT,
        ",".join(all_rules),
        RUFF_FLAG_EXCLUDE,
        ",".join(FALLBACK_EXCLUDE_PATHS),
        RUFF_FLAG_LINE_LENGTH,
        str(FALLBACK_LINE_LENGTH),
    ]

    format_args = [
        RUFF_FLAG_LINE_LENGTH,
        str(FALLBACK_LINE_LENGTH),
    ]

    python_version = get_python_version()
    lint_args.extend([RUFF_FLAG_TARGET_VERSION, python_version])
    format_args.extend([RUFF_FLAG_TARGET_VERSION, python_version])

    return lint_args, format_args


def print_fallback_mode_info() -> None:
    """Print information about the fallback mode configuration."""
    python_version = get_python_version()

    info_lines = [
        "\n<ruff-fallback-mode>",
        "Using fallback ruff configuration with CLI options:",
        "Applied settings:",
        f"\t- Lint rules: {','.join(FALLBACK_LINT_RULES)}",
        f"\t- Excluded paths: {','.join(FALLBACK_EXCLUDE_PATHS)}",
        f"\t- Line length: {FALLBACK_LINE_LENGTH}",
        f"\t- Target Python version: {python_version}",
    ]

    info_lines.extend(
        [
            "Reason: No venv found, ruff not in expected locations, or no ruff config in pyproject.toml",
            "</ruff-fallback-mode>\n",
        ]
    )

    print("\n".join(info_lines))  # fallback 을 했다는걸 Claude Code 가 알 필요는 없음, not stderr


def run_ruff_command(ruff_path: str, args: list[str]) -> tuple[str, int]:
    """Run a ruff command and return output and exit code.

    Args:
        ruff_path: Path to ruff executable
        args: Command line arguments for ruff

    Returns:
        Tuple of (combined_output, exit_code)
    """
    try:
        result = subprocess.run(
            [ruff_path] + args,
            check=False,
            capture_output=True,
            text=True,
            cwd=".",
        )
        return result.stdout + result.stderr, result.returncode
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return f"Error running ruff: {e}", 1


def _is_valid_python_file(file_path: str) -> bool:
    """Check if path is a valid Python file."""
    if not file_path or not file_path.endswith(".py"):
        return False
    return Path(file_path).exists()


def _build_complete_error_message(results: RuffResults) -> str:
    """Build complete error message including reminders."""
    message = _build_error_message(results)

    if not message.strip():
        return ""

    if results.lint_exit_code != 0 or results.has_unused_imports:
        message_parts = [message, "\n\n", _get_error_fix_reminder()]
        return "".join(message_parts)

    return message


def _build_error_message(results: RuffResults) -> str:
    """Build comprehensive error message from ruff results."""
    message_parts: list[str] = []

    if results.has_auto_fixes and results.lint_exit_code == 0:
        if results.lint_output:
            message_parts.append("\n")
            auto_fix_content = (
                results.lint_output + "\n\n"
                "FILE AUTOMATICALLY MODIFIED\n"
                "The file has been changed by ruff auto-fix.\n"
                "REQUIRED ACTION: Use Read() to get the latest file content before any Edit() operations.\n"
                "Attempting to edit without reading first will cause conflicts or errors."
            )
            message_parts.append(_wrap_in_xml_tags("ruff-auto-fixed", auto_fix_content))
            message_parts.append("\n")
    elif results.lint_exit_code != 0 and results.lint_output:
        message_parts.append("\n")
        message_parts.append(_wrap_in_xml_tags("ruff-lint", results.lint_output))
        message_parts.append("\n")

    if results.format_exit_code != 0 and results.format_output:
        message_parts.append("\n")
        message_parts.append(_wrap_in_xml_tags("ruff-format", results.format_output))
        message_parts.append("\n")

    if results.has_unused_imports:
        message_parts.append(_get_unused_imports_warning())

    return "".join(message_parts)


def _get_unused_imports_warning() -> str:
    """Get warning message for unused imports."""
    return """
CRITICAL ERROR: UNUSED IMPORTS DETECTED - IMMEDIATE ACTION REQUIRED

You have added import statements that are NOT being used anywhere in the code.
This is a CODE QUALITY VIOLATION and MUST be fixed NOW.

**WHAT YOU MUST DO RIGHT NOW:**

Option 1: DELETE the unused import immediately
Option 2: ADD code that actually USES the import

DO NOT LEAVE UNUSED IMPORTS IN THE CODE.

**WHY THIS IS CRITICAL:**
- Unused imports are DEAD CODE
- They pollute the namespace
- They confuse other developers
- They slow down module loading
- They will FAIL code review
- They indicate INCOMPLETE implementation

**YOU HAVE TWO CHOICES - PICK ONE:**

1. **Remove the import** - If you don't need it, DELETE it immediately
2. **Use the import** - Write the code that actually needs this module

**THERE IS NO THIRD OPTION.**

Unused imports are NOT acceptable. Period.

If you added an import, you MUST use it in the same edit.
If you're not using it yet, DON'T add the import yet.

**CORRECT WORKFLOW:**
1. FIRST: Write the code that USES the module
2. THEN: Add the import statement along with the usage

<examples>
<bad-example description="Calling Edit tools with import only code in first Edit()">
  Edit(
    file_path='app.py',
    old_string='def process():\\n    pass',
    new_string='from typing import List\\n\\ndef process():\\n    pass'
  )
  # Import added but not used yet on here - linter REMOVES THIS IMMEDIATELY
</bad-example>

<good-example description="Single Edit() with both import and usage together">
  Edit(
    file_path='app.py',
    old_string='def process():\\n    pass',
    new_string='from typing import List\\n\\ndef process(items: List[str]):\\n    return items'
  )
  # Both import and usage added together - linter accepts this
</good-example>

<good-example description="For big replaces: Add usage code first, then add import">
  # Step 1: Add the actual usage code first (without import)
  Edit(
    file_path='large_module.py',
    old_string='def calculate():\\n    return 0',
    new_string='def calculate(data: List[int]):\\n    return sum(data)'
  )

  # Step 2: Add the import immediately after
  Edit(
    file_path='large_module.py',
    old_string='import os\\nimport sys',
    new_string='import os\\nimport sys\\nfrom typing import List'
  )
  # Now import is added with existing usage - linter accepts this
</good-example>
</examples>

**WARNING**: FILE HAS BEEN MODIFIED - Use Read() before attempting Edit again.
""".strip()


def _get_error_fix_reminder() -> str:
    """Get reminder message about fixing errors."""
    return (
        "**CRITICAL: DO NOT IGNORE THESE ERRORS!**\n"
        "You MUST fix ALL lint errors and formatting issues shown above.\n"
        "These are not warnings - they are REQUIRED fixes.\n"
        "The code WILL NOT pass CI/CD until these are resolved.\n\n"
        "IMPORTANT: If the file was auto-fixed, use Read() before Edit to avoid conflicts."
    )


def _wrap_in_xml_tags(tag: str, content: str) -> str:
    """Wrap content in XML-like tags for structured output."""
    return f"<{tag}>\n{content}\n</{tag}>"


def _get_current_python_version() -> str:
    """Get the current Python version in ruff format."""
    return f"py{sys.version_info.major}{sys.version_info.minor}"


def _extract_version_from_string(requires_python: str) -> str | None:
    """Extract version number from a requirements string."""
    if version_match := VERSION_WITH_OPERATOR_PATTERN.search(requires_python):
        return version_match.group(1)
    if version_match := VERSION_WITHOUT_OPERATOR_PATTERN.match(requires_python.strip()):
        return version_match.group(1)
    return None


def _format_python_version(version: str) -> str | None:
    """Format version string to ruff format (e.g., 'py311')."""
    version_parts = version.split(".")[:2]
    if len(version_parts) < 2:
        return None

    major, minor = version_parts[0], version_parts[1]
    return f"py{major}{minor}"


def _get_ruff_target_version(config: PyprojectConfig) -> str | None:
    """Extract target version from ruff configuration."""
    match config:
        case {"tool": {"ruff": {"target-version": str(version)}}}:
            return version
        case _:
            return None


def _get_project_python_version(config: PyprojectConfig) -> str | None:
    """Extract Python version from project configuration."""
    match config:
        case {"project": {"requires-python": str(requires_python)}} if requires_python:
            version = _extract_version_from_string(requires_python)
            if version:
                return _format_python_version(version)
            return None
        case _:
            return None


if __name__ == "__main__":
    main()
