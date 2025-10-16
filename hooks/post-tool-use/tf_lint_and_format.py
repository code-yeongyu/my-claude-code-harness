#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

# Configuration constants
TERRAFORM_EXTENSIONS: list[str] = [".tf", ".tfvars"]
TF_BINARY = "terraform"

# Terraform commands
TF_CMD_FMT = "fmt"
TF_CMD_VALIDATE = "validate"
TF_CMD_INIT = "init"

# Terraform flags
TF_FLAG_CHECK = "-check"
TF_FLAG_DIFF = "-diff"
TF_FLAG_RECURSIVE = "-recursive"
TF_FLAG_LIST = "-list=false"
TF_FLAG_WRITE = "-write=true"
TF_FLAG_BACKEND = "-backend=false"
TF_FLAG_UPGRADE = "-upgrade"


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
    tool_response: dict[str, object]


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


@dataclass
class TerraformConfiguration:
    terraform_path: str | None
    working_dir: Path
    is_module: bool


@dataclass
class TerraformResults:
    format_output: str
    format_exit_code: int
    validate_output: str
    validate_exit_code: int
    has_format_changes: bool
    has_validation_errors: bool


ClaudeCodeToolInput = WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput


def main() -> None:
    """Main entry point for the terraform hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    input_data = sys.stdin.read()
    file_path = get_target_file_path(input_data)
    if not file_path:
        print("[terraform-hook] Skipping: No valid Terraform file path provided or file is not .tf/.tfvars")
        sys.exit(0)

    config = resolve_terraform_configuration(Path(file_path))
    if not config.terraform_path:
        print("[terraform-hook] Skipping: terraform not found in system PATH")
        sys.exit(0)

    results = execute_terraform_operations(config, file_path)
    handle_results_and_exit(results)


def get_target_file_path(input_data: str) -> str | None:
    """Extract and validate the target Terraform file path from input.

    Returns:
        The file path if it's a valid Terraform file, None otherwise
    """
    if not input_data:
        return None

    try:
        input_json = json.loads(input_data)
        data = PostToolUseInput(
            session_id=input_json["session_id"],
            tool_name=input_json["tool_name"],
            transcript_path=input_json["transcript_path"],
            cwd=input_json["cwd"],
            hook_event_name=input_json["hook_event_name"],
            tool_input=input_json["tool_input"],
            tool_response=input_json["tool_response"],
        )
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

    return file_path if _is_valid_terraform_file(file_path) else None


def resolve_terraform_configuration(file_path: Path) -> TerraformConfiguration:
    """Resolve terraform tools configuration."""
    terraform_path = shutil.which(TF_BINARY)

    working_dir = file_path.parent
    is_module = _is_terraform_module(working_dir)

    return TerraformConfiguration(
        terraform_path=terraform_path,
        working_dir=working_dir,
        is_module=is_module,
    )


def execute_terraform_operations(config: TerraformConfiguration, file_path: Path | str) -> TerraformResults:
    """Execute all terraform operations and collect results.

    Args:
        config: Terraform configuration
        file_path: Path to the specific file to check
    """
    format_output = ""
    format_exit_code = 0
    validate_output = ""
    validate_exit_code = 0
    has_format_changes = False

    # Check formatting
    if config.terraform_path:
        # Check if formatting is needed
        _, exit_code = run_terraform_command(
            config.terraform_path,
            [TF_CMD_FMT, TF_FLAG_CHECK, TF_FLAG_DIFF, str(file_path)],
            config.working_dir,
        )
        has_format_changes = exit_code != 0

        if has_format_changes:
            # Apply formatting
            _, _ = run_terraform_command(
                config.terraform_path,
                [TF_CMD_FMT, TF_FLAG_WRITE, str(file_path)],
                config.working_dir,
            )
            format_output = f"Reformatted: {file_path}"
            format_exit_code = 1  # Mark as changed

        # Validate configuration (only if not a module)
        if not config.is_module:
            # Try to init first (ignore errors)
            _, _ = run_terraform_command(
                config.terraform_path,
                [TF_CMD_INIT, TF_FLAG_BACKEND],
                config.working_dir,
            )

            validate_output, validate_exit_code = run_terraform_command(
                config.terraform_path,
                [TF_CMD_VALIDATE],
                config.working_dir,
            )

    return TerraformResults(
        format_output=format_output,
        format_exit_code=format_exit_code,
        validate_output=validate_output,
        validate_exit_code=validate_exit_code,
        has_format_changes=has_format_changes,
        has_validation_errors=validate_exit_code != 0,
    )


def handle_results_and_exit(results: TerraformResults) -> None:
    """Handle terraform results and exit with appropriate code."""
    has_only_formatting = results.has_format_changes and not results.has_validation_errors

    if has_only_formatting:
        if results.format_output:
            print(
                f"\n{_wrap_in_xml_tags('terraform-format', results.format_output)}\n",
                file=sys.stderr,
            )
        sys.exit(2)

    message = _build_complete_error_message(results)

    if not message:
        print("[terraform-hook] Success: No issues found")
        sys.exit(0)

    if results.has_format_changes and not results.has_validation_errors:
        print(message, file=sys.stderr)
        sys.exit(2)

    if results.has_validation_errors:
        print(message, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


def run_terraform_command(binary_path: str, args: list[str], working_dir: Path) -> tuple[str, int]:
    """Run a terraform command and return output and exit code.

    Args:
        binary_path: Path to terraform executable
        args: Command line arguments
        working_dir: Working directory for the command

    Returns:
        Tuple of (combined_output, exit_code)
    """
    try:
        result = subprocess.run(
            [binary_path] + args,
            check=False,
            capture_output=True,
            text=True,
            cwd=str(working_dir),
        )
        return result.stdout + result.stderr, result.returncode
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return f"Error running {binary_path}: {e}", 1


def _is_valid_terraform_file(file_path: str) -> bool:
    """Check if path is a valid Terraform file."""
    if not file_path:
        return False

    path = Path(file_path)
    if not path.exists():
        return False

    return any(file_path.endswith(ext) for ext in TERRAFORM_EXTENSIONS)


def _is_terraform_module(directory: Path) -> bool:
    """Check if directory is a Terraform module (no .tf files with provider/backend)."""
    tf_files = list(directory.glob("*.tf"))

    for tf_file in tf_files:
        try:
            content = tf_file.read_text()
            # Simple heuristic: if it has provider or backend, it's not a module
            if "provider " in content or "backend " in content:
                return False
        except Exception:
            continue

    # If we have .tf files but no provider/backend, likely a module
    return len(tf_files) > 0


def _build_complete_error_message(results: TerraformResults) -> str:
    """Build complete error message including reminders."""
    message = _build_error_message(results)

    if not message.strip():
        return ""

    if results.has_validation_errors:
        message_parts = [message, "\n\n", _get_error_fix_reminder()]
        return "".join(message_parts)

    return message


def _build_error_message(results: TerraformResults) -> str:
    """Build comprehensive error message from terraform results."""
    message_parts: list[str] = []

    if results.has_format_changes and results.format_output:
        message_parts.append("\n")
        message_parts.append(_wrap_in_xml_tags("terraform-format", results.format_output))
        message_parts.append("\n")

    if results.has_validation_errors and results.validate_output:
        message_parts.append("\n")
        message_parts.append(_wrap_in_xml_tags("terraform-validate", results.validate_output))
        message_parts.append("\n")

    return "".join(message_parts)


def _get_error_fix_reminder() -> str:
    """Get reminder message about fixing errors."""
    return (
        "**CRITICAL: DO NOT IGNORE THESE TERRAFORM ERRORS!**\n"
        "You MUST fix ALL validation errors shown above.\n"
        "These are not warnings - they are REQUIRED fixes.\n"
        "The infrastructure code WILL NOT deploy until these are resolved."
    )


def _wrap_in_xml_tags(tag: str, content: str) -> str:
    """Wrap content in XML-like tags for structured output."""
    return f"<{tag}>\n{content}\n</{tag}>"


if __name__ == "__main__":
    main()
