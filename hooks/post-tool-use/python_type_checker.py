#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["toml>=0.10.2"]
# ///

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import toml

# Configuration constants
DEFAULT_TIMEOUT_MS = 60000
VENV_BASEDPYRIGHT_PATHS: list[Path] = [
    Path(".venv/bin/basedpyright"),
    Path("venv/bin/basedpyright"),
]
if sys.platform == "win32":
    VENV_BASEDPYRIGHT_PATHS.extend(
        [
            Path(".venv/Scripts/basedpyright.exe"),
            Path("venv/Scripts/basedpyright.exe"),
        ]
    )

# Fallback configuration defaults
# Note: Only CLI-supported options are included here
FALLBACK_CONFIG = {
    "venvpath": ".",
    "skipunannotated": False,
}

# Default exclude patterns for fallback mode
DEFAULT_EXCLUDE_PATTERNS = [
    "**/__pycache__",
    "**/node_modules",
    "**/venv",
    "**/.venv",
    "**/migrations/**",
    "**/.git",
    "**/dist",
    "**/build",
    "**/.tox",
    "**/*.egg-info",
]

PYPROJECT_PATH = Path("pyproject.toml")
BASEDPYRIGHT_CONFIG_NAMES = [
    "basedpyrightconfig.json",
    "pyrightconfig.json",
]

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


class PyprojectToml(TypedDict):
    tool: NotRequired[ToolConfig]
    project: NotRequired[ProjectConfig]


class ToolConfig(TypedDict):
    basedpyright: NotRequired[dict[str, Any]]
    pyright: NotRequired[dict[str, Any]]
    mypy: NotRequired[dict[str, Any]]


class ProjectConfig(TypedDict):
    requires_python: NotRequired[str]


@dataclass
class TypeCheckConfiguration:
    executable_path: str
    args: list[str]
    use_fallback: bool


@dataclass
class TypeCheckResults:
    output: str
    exit_code: int


ClaudeCodeToolInput = WriteToolInput | EditToolInput | MultiEditToolInput | NotebookEditToolInput
PyprojectConfig = PyprojectToml | dict[str, Any]


def main() -> None:
    """Main entry point for the basedpyright type checker hook."""
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    input_data = sys.stdin.read()
    file_path = get_target_file_path(input_data)
    if not file_path:
        print("[typecheck-hook] Skipping: No valid Python file path provided or file is not .py/.pyi")
        sys.exit(0)

    # Check if file should be excluded
    project_root = find_project_root(Path(file_path))
    if should_exclude_file(file_path, project_root):
        sys.exit(0)

    config = resolve_typecheck_configuration(Path(file_path))
    if not config:
        print("[typecheck-hook] Skipping: basedpyright not found in .venv or system PATH")
        sys.exit(0)

    results = execute_typecheck(config, file_path)
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


def resolve_typecheck_configuration(file_path: Path) -> TypeCheckConfiguration | None:
    """Resolve basedpyright executable path and configuration."""
    project_root = find_project_root(file_path)

    if has_mypy_config(project_root):
        print("[typecheck-hook] Skipping: Project uses mypy (found [tool.mypy] in pyproject.toml)")
        print("[typecheck-hook] basedpyright check disabled to avoid conflicts with mypy")
        return None

    basedpyright_path, needs_fallback = find_basedpyright_executable()
    if not basedpyright_path:
        basedpyright_path = shutil.which("basedpyright")
        if not basedpyright_path:
            return None
        needs_fallback = True

    if needs_fallback or should_use_fallback_config(project_root):
        args = get_fallback_args(project_root)
        print_fallback_mode_info(project_root)
        use_fallback = True
    else:
        args = ["--level", "error"]
        config_path = find_config_file(project_root)
        if config_path:
            args.extend(["--project", str(config_path)])
        else:
            args.extend(["--project", str(project_root)])
        use_fallback = False

    return TypeCheckConfiguration(
        executable_path=basedpyright_path,
        args=args,
        use_fallback=use_fallback,
    )


def execute_typecheck(config: TypeCheckConfiguration, file_path: str) -> TypeCheckResults:
    """Execute basedpyright and collect results."""
    output, exit_code = run_basedpyright_command(config.executable_path, config.args + [file_path])

    return TypeCheckResults(
        output=output,
        exit_code=exit_code,
    )


def handle_results_and_exit(results: TypeCheckResults) -> None:
    """Handle type check results and exit with appropriate code."""
    if results.exit_code == 0:
        print("[typecheck-hook] Success: No type errors found")
        sys.exit(0)

    message_parts = []
    if results.output:
        message_parts.append("\n")
        message_parts.append(_wrap_in_xml_tags("basedpyright", results.output))
        message_parts.append("\n")

    message_parts.append(_get_error_fix_reminder())

    print("".join(message_parts), file=sys.stderr)
    sys.exit(2)


def find_basedpyright_executable() -> tuple[str | None, bool]:
    """Find basedpyright executable in virtual environment.

    Returns:
        Tuple of (path_to_basedpyright, needs_fallback_config)
        If basedpyright is not found, returns (None, True)
    """
    if env_path := os.environ.get("BASEDPYRIGHT_PATH"):
        if Path(env_path).exists() and os.access(env_path, os.X_OK):
            return env_path, False

    if venv_basedpyright := next((p for p in VENV_BASEDPYRIGHT_PATHS if p.exists()), None):
        return str(venv_basedpyright.absolute()), False

    system_basedpyright = shutil.which("basedpyright")
    if system_basedpyright:
        return system_basedpyright, True

    return None, True


def should_use_fallback_config(project_root: Path) -> bool:
    """Check if fallback configuration should be used.

    Returns:
        True if fallback config should be used (no config file or no basedpyright config)
    """
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path) as f:
                config: PyprojectConfig = toml.load(f)

            has_basedpyright_config = "tool" in config and (
                "basedpyright" in config["tool"] or "pyright" in config["tool"]
            )
            if has_basedpyright_config:
                return False
        except (OSError, toml.TomlDecodeError, KeyError, TypeError):
            pass

    for config_name in BASEDPYRIGHT_CONFIG_NAMES:
        config_path = project_root / config_name
        if config_path.exists():
            return False

    return True


def get_fallback_args(project_root: Path) -> list[str]:
    """Get fallback basedpyright arguments.

    Returns:
        List of CLI arguments for basedpyright
    """
    args = ["--level", "error"]

    for key, value in FALLBACK_CONFIG.items():
        if key == "skipunannotated" and value:
            # skipunannotated is a flag option, not a value option
            args.append(f"--{key}")
        elif key == "venvpath":
            args.extend([f"--{key}", str(value)])

    python_version = get_python_version(project_root)
    if python_version:
        args.extend(["--pythonversion", python_version])

    return args


def print_fallback_mode_info(project_root: Path) -> None:
    """Print information about fallback mode configuration."""
    python_version = get_python_version(project_root)

    info_lines = [
        "\n<basedpyright-fallback-mode>",
        "Using fallback basedpyright configuration with CLI options:",
        "Applied settings:",
        "\t- Level: error only",
        f"\t- venvpath: {FALLBACK_CONFIG['venvpath']}",
        f"\t- skipunannotated: {FALLBACK_CONFIG['skipunannotated']}",
        f"\t- Target Python version: {python_version}",
    ]

    config_path = find_config_file(project_root)
    if config_path:
        info_lines.append(f"\t- Configuration file found: {config_path}")
    else:
        info_lines.append("\t- No basedpyright/pyright configuration found")

    info_lines.extend(
        [
            "Reason: No venv found, basedpyright not in expected locations, or no config in pyproject.toml",
            "</basedpyright-fallback-mode>\n",
        ]
    )

    print("\n".join(info_lines))


def run_basedpyright_command(basedpyright_path: str, args: list[str]) -> tuple[str, int]:
    """Run a basedpyright command and return output and exit code.

    Args:
        basedpyright_path: Path to basedpyright executable
        args: Command line arguments for basedpyright

    Returns:
        Tuple of (combined_output, exit_code)
    """
    timeout_ms = int(os.environ.get("BASEDPYRIGHT_TIMEOUT_MS", DEFAULT_TIMEOUT_MS))

    try:
        result = subprocess.run(
            [basedpyright_path] + args,
            check=False,
            capture_output=True,
            text=True,
            cwd=".",
            timeout=timeout_ms / 1000,
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return f"basedpyright exceeded timeout ({timeout_ms}ms)", 2
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return f"Error running basedpyright: {e}", 2


def find_project_root(file_path: Path) -> Path:
    """Find project root by looking for pyproject.toml or .git."""
    current = file_path.parent

    while current != current.parent:
        if (current / "pyproject.toml").exists() or (current / ".git").exists():
            return current
        current = current.parent

    return file_path.parent


def find_config_file(project_root: Path) -> Path | None:
    """Find basedpyright or pyright config file."""
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, encoding="utf-8") as f:
                data = toml.loads(f.read())

            if "tool" in data and ("basedpyright" in data["tool"] or "pyright" in data["tool"]):
                return pyproject_path
        except Exception:
            pass

    for config_name in BASEDPYRIGHT_CONFIG_NAMES:
        config_path = project_root / config_name
        if config_path.exists():
            return config_path

    return None


def has_mypy_config(project_root: Path) -> bool:
    """Check if project has mypy configuration."""
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, encoding="utf-8") as f:
                data = toml.loads(f.read())

            if "tool" in data and "mypy" in data["tool"]:
                return True
        except Exception:
            pass

    mypy_ini = project_root / "mypy.ini"
    if mypy_ini.exists():
        return True

    setup_cfg = project_root / "setup.cfg"
    if setup_cfg.exists():
        try:
            with open(setup_cfg, encoding="utf-8") as f:
                content = f.read()
                if "[mypy" in content:
                    return True
        except Exception:
            pass

    return False


def get_python_version(project_root: Path) -> str:
    """Get target Python version from pyproject.toml or current environment."""
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return _get_current_python_version()

    try:
        with open(pyproject_path, encoding="utf-8") as f:
            config = toml.loads(f.read())

        target_version = _get_basedpyright_target_version(config)
        if target_version:
            return target_version

        target_version = _get_pyright_target_version(config)
        if target_version:
            return target_version

        project_version = _get_project_python_version(config)
        if project_version:
            return project_version

        return _get_current_python_version()
    except (OSError, KeyError, TypeError):
        return _get_current_python_version()


def _wrap_in_xml_tags(tag: str, content: str) -> str:
    """Wrap content in XML-like tags for structured output."""
    return f"<{tag}>\n{content}\n</{tag}>"


def _is_valid_python_file(file_path: str) -> bool:
    """Check if path is a valid Python file."""
    if not file_path:
        return False

    path = Path(file_path)
    return path.exists() and path.suffix in (".py", ".pyi")


def _get_error_fix_reminder() -> str:
    """Get reminder message about fixing type errors."""
    return (
        "\n**CRITICAL: DO NOT IGNORE THESE TYPE ERRORS!**\n"
        "You MUST fix ALL type errors shown above.\n"
        "These errors indicate potential runtime issues and must be resolved.\n"
        "The code may fail at runtime if these type errors are not addressed."
    )


def _get_current_python_version() -> str:
    """Get the current Python version in basedpyright format."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _get_basedpyright_target_version(config: dict[str, Any]) -> str | None:
    """Extract target version from basedpyright configuration."""
    try:
        if "tool" in config and "basedpyright" in config["tool"]:
            basedpyright_config = config["tool"]["basedpyright"]
            if "pythonVersion" in basedpyright_config:
                return basedpyright_config["pythonVersion"]
    except (KeyError, TypeError):
        pass
    return None


def _get_pyright_target_version(config: dict[str, Any]) -> str | None:
    """Extract target version from pyright configuration."""
    try:
        if "tool" in config and "pyright" in config["tool"]:
            pyright_config = config["tool"]["pyright"]
            if "pythonVersion" in pyright_config:
                return pyright_config["pythonVersion"]
    except (KeyError, TypeError):
        pass
    return None


def _get_project_python_version(config: dict[str, Any]) -> str | None:
    """Extract Python version from project configuration."""
    try:
        if "project" in config and "requires-python" in config["project"]:
            requires_python = config["project"]["requires-python"]
            if requires_python:
                version = _extract_version_from_string(requires_python)
                if version:
                    return version
    except (KeyError, TypeError):
        pass
    return None


def _extract_version_from_string(requires_python: str) -> str | None:
    """Extract version number from a requirements string."""
    if version_match := VERSION_WITH_OPERATOR_PATTERN.search(requires_python):
        return version_match.group(1)
    if version_match := VERSION_WITHOUT_OPERATOR_PATTERN.match(requires_python.strip()):
        return version_match.group(1)
    return None


def should_exclude_file(file_path: str, project_root: Path) -> bool:
    """Check if file should be excluded based on exclude patterns.

    Args:
        file_path: Absolute path to the file being checked
        project_root: Root directory of the project

    Returns:
        True if file should be excluded, False otherwise
    """
    # Get exclude patterns from configuration
    exclude_patterns = get_exclude_patterns(project_root)

    # Convert absolute path to relative path from project root
    try:
        relative_path = Path(file_path).relative_to(project_root)
        relative_path_str = str(relative_path).replace(os.sep, "/")
    except ValueError:
        # File is outside project root, don't exclude
        return False

    # Check if file matches any exclude pattern
    for pattern in exclude_patterns:
        # Handle both glob patterns and simple patterns
        normalized_pattern = pattern.replace(os.sep, "/")

        # Check direct match
        if fnmatch.fnmatch(relative_path_str, normalized_pattern):
            return True

        # Check if any parent directory matches (for patterns like "**/migrations/**")
        if "**" in normalized_pattern:
            # Convert ** pattern to work with fnmatch
            cleaned_pattern = normalized_pattern.replace("**/", "").replace("/**", "")
            if "/" in cleaned_pattern:
                # Check if any part of the path matches
                path_parts = relative_path_str.split("/")
                for i in range(len(path_parts)):
                    partial_path = "/".join(path_parts[: i + 1])
                    if fnmatch.fnmatch(partial_path, cleaned_pattern.rstrip("/")):
                        return True
                    if fnmatch.fnmatch(relative_path_str, normalized_pattern.replace("**", "*")):
                        return True
            else:
                # Simple directory name pattern
                if cleaned_pattern in relative_path_str.split("/"):
                    return True

    return False


def get_exclude_patterns(project_root: Path) -> list[str]:
    """Get exclude patterns from project configuration.

    Args:
        project_root: Root directory of the project

    Returns:
        List of exclude patterns
    """
    exclude_patterns = []

    # Try to load from pyproject.toml
    pyproject_path = project_root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            with open(pyproject_path, encoding="utf-8") as f:
                config = toml.load(f)

            # Check for basedpyright exclude patterns
            if "tool" in config:
                if "basedpyright" in config["tool"]:
                    basedpyright_config = config["tool"]["basedpyright"]
                    if "exclude" in basedpyright_config:
                        exclude = basedpyright_config["exclude"]
                        if isinstance(exclude, list):
                            exclude_patterns.extend(exclude)
                        elif isinstance(exclude, str):
                            exclude_patterns.append(exclude)
                    # Return early if basedpyright config exists with exclude
                    return exclude_patterns if exclude_patterns else DEFAULT_EXCLUDE_PATTERNS

                # Check for pyright exclude patterns if basedpyright not found
                if "pyright" in config["tool"]:
                    pyright_config = config["tool"]["pyright"]
                    if "exclude" in pyright_config:
                        exclude = pyright_config["exclude"]
                        if isinstance(exclude, list):
                            exclude_patterns.extend(exclude)
                        elif isinstance(exclude, str):
                            exclude_patterns.append(exclude)
                    # Return early if pyright config exists
                    return exclude_patterns if exclude_patterns else DEFAULT_EXCLUDE_PATTERNS

        except (OSError, toml.TomlDecodeError, KeyError, TypeError):
            pass

    # Try to load from basedpyright/pyright config files
    for config_name in BASEDPYRIGHT_CONFIG_NAMES:
        config_path = project_root / config_name
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    config = json.load(f)
                    if "exclude" in config:
                        exclude = config["exclude"]
                        if isinstance(exclude, list):
                            exclude_patterns.extend(exclude)
                        elif isinstance(exclude, str):
                            exclude_patterns.append(exclude)
                    # Return if config file found
                    return exclude_patterns if exclude_patterns else DEFAULT_EXCLUDE_PATTERNS
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass

    # If no configuration found, return default patterns
    return DEFAULT_EXCLUDE_PATTERNS


if __name__ == "__main__":
    main()
