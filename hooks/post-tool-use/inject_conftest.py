#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "orjson",
#     "toml>=0.10.2",
# ]
# ///
# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

import orjson
import toml


class ReadToolInput(TypedDict):
    file_path: str


class PostToolUseInput(TypedDict):
    session_id: str
    tool_name: str
    transcript_path: str
    cwd: str
    hook_event_name: str
    tool_input: ReadToolInput
    tool_response: dict[str, Any]


class ToolUseBlock(TypedDict):
    type: Literal["tool_use"]
    name: Literal["Read"] | str
    id: str
    input: dict[str, Any]


class ToolResultBlock(TypedDict):
    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[dict[str, Any]]
    is_error: NotRequired[bool]


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


ContentBlock = ToolUseBlock | ToolResultBlock | TextBlock


class AssistantMessage(TypedDict):
    role: Literal["assistant"]
    content: list[ContentBlock]


class UserMessage(TypedDict):
    role: Literal["user"]
    content: str | list[ContentBlock]


class TranscriptEntry(TypedDict):
    type: Literal["user", "assistant"]
    message: AssistantMessage | UserMessage
    timestamp: NotRequired[str]


class RawTranscriptEntry(TypedDict):
    type: str
    message: dict[str, Any]
    timestamp: NotRequired[str]


class ConftestInfo(TypedDict):
    path: str
    distance: int
    hash: str
    last_modified: str


class ProjectRootFinder:
    PROJECT_ROOT_MARKERS = ["pyproject.toml", ".venv", ".git"]

    @classmethod
    def find_root(cls, start_path: Path) -> Path | None:
        current_dir = start_path.resolve()

        while current_dir != current_dir.parent:
            for marker in cls.PROJECT_ROOT_MARKERS:
                marker_path = current_dir / marker
                if marker_path.exists() and (
                    marker_path.is_file() or marker_path.is_dir()
                ):
                    return current_dir
            current_dir = current_dir.parent

        return None


class TestFileDetector:
    DEFAULT_TEST_PATTERNS = ["test_*.py", "*_test.py", "*_tests.py", "tests.py"]

    def __init__(self) -> None:
        self.test_patterns = self._load_test_patterns()

    def _load_test_patterns(self) -> list[str]:
        pyproject_path = Path("pyproject.toml")

        if not pyproject_path.exists():
            return self.DEFAULT_TEST_PATTERNS

        try:
            with open(pyproject_path, encoding="utf-8") as f:
                config: dict[str, Any] = toml.load(f)

            tool_config = config.get("tool", {})
            pytest_config = tool_config.get("pytest", {})
            ini_options = pytest_config.get("ini_options", {})

            python_files = ini_options.get("python_files", [])
            if isinstance(python_files, list) and python_files:
                return python_files

            return self.DEFAULT_TEST_PATTERNS
        except Exception:
            return self.DEFAULT_TEST_PATTERNS

    def is_test_file(self, file_path: str) -> bool:
        if not file_path.endswith(".py"):
            return False

        path = Path(file_path)
        file_name = path.name

        if (
            file_name.startswith("test_")
            or file_name.endswith("_test.py")
            or file_name.endswith("_tests.py")
            or file_name == "tests.py"
        ):
            return True

        for pattern in self.test_patterns:
            cleaned_pattern = pattern.replace("*", "")
            if cleaned_pattern in file_name:
                return True

        return False


class ConftestFinder:
    PACKAGE_CONFTEST_DISTANCE_OFFSET = 1000

    def __init__(self, test_file_path: str, cwd: str | None = None) -> None:
        self.test_file = Path(test_file_path).resolve()
        self.project_root = (
            Path(cwd).resolve()
            if cwd
            else ProjectRootFinder.find_root(self.test_file.parent)
        )

    def find_conftests(self) -> list[ConftestInfo]:
        if not self.project_root:
            return []

        conftest_infos = self._collect_upward_conftests()
        existing_paths = {info["path"] for info in conftest_infos}

        package_conftests = self._collect_mirrored_package_conftests(existing_paths)
        conftest_infos.extend(package_conftests)

        return sorted(conftest_infos, key=lambda x: x["distance"])

    def _collect_upward_conftests(self) -> list[ConftestInfo]:
        conftest_infos: list[ConftestInfo] = []

        if not self.project_root:
            return conftest_infos

        try:
            self.test_file.relative_to(self.project_root)
        except ValueError:
            return conftest_infos

        current_dir = self.test_file.parent
        distance = 0

        while current_dir >= self.project_root and current_dir != current_dir.parent:
            conftest_path = current_dir / "conftest.py"
            if conftest_path.is_file():
                try:
                    relative_path = conftest_path.relative_to(self.project_root)
                    path_str = (
                        str(relative_path)
                        if relative_path != Path(".")
                        else "conftest.py"
                    )
                except ValueError:
                    path_str = str(conftest_path)

                try:
                    file_content = conftest_path.read_bytes()
                    file_hash = hashlib.sha256(file_content).hexdigest()[:16]
                    last_modified = datetime.fromtimestamp(
                        conftest_path.stat().st_mtime
                    ).isoformat()
                except OSError:
                    file_hash = "unknown"
                    last_modified = "unknown"

                conftest_infos.append(
                    ConftestInfo(
                        path=path_str,
                        distance=distance,
                        hash=file_hash,
                        last_modified=last_modified,
                    )
                )

            if current_dir == self.project_root:
                break

            current_dir = current_dir.parent
            distance += 1

        return conftest_infos

    def _collect_mirrored_package_conftests(
        self, existing_paths: set[str]
    ) -> list[ConftestInfo]:
        conftest_infos: list[ConftestInfo] = []

        if not self.project_root:
            return conftest_infos

        try:
            relative_path = self.test_file.relative_to(self.project_root)
        except ValueError:
            return conftest_infos

        if not relative_path.parts or relative_path.parts[0] != "tests":
            return conftest_infos

        test_subdirectory_parts = list(relative_path.parts[1:-1])

        if not test_subdirectory_parts:
            return conftest_infos

        current_pkg_path = self.project_root

        for i, part in enumerate(test_subdirectory_parts):
            current_pkg_path = current_pkg_path / part
            pkg_conftest = current_pkg_path / "conftest.py"

            if pkg_conftest.is_file():
                try:
                    relative_path = pkg_conftest.relative_to(Path.cwd())
                    path_str = str(relative_path)
                except ValueError:
                    path_str = str(pkg_conftest)

                if path_str not in existing_paths:
                    try:
                        file_content = pkg_conftest.read_bytes()
                        file_hash = hashlib.sha256(file_content).hexdigest()[:16]
                        last_modified = datetime.fromtimestamp(
                            pkg_conftest.stat().st_mtime
                        ).isoformat()
                    except OSError:
                        file_hash = "unknown"
                        last_modified = "unknown"

                    conftest_infos.append(
                        ConftestInfo(
                            path=path_str,
                            distance=self.PACKAGE_CONFTEST_DISTANCE_OFFSET + i,
                            hash=file_hash,
                            last_modified=last_modified,
                        )
                    )

        return conftest_infos


class ConftestInjector:
    def __init__(self, project_root: Path, transcript_path: str) -> None:
        self.project_root = project_root
        self.transcript_path = Path(transcript_path)

    def _has_conftest_been_read(self, conftest_path: Path) -> bool:
        if not self.transcript_path.exists():
            return False

        try:
            conftest_path_str = str(conftest_path)
            transcript_content = self.transcript_path.read_text(encoding="utf-8")

            for line in transcript_content.splitlines():
                if not line.strip():
                    continue

                try:
                    entry: RawTranscriptEntry = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                if not isinstance(entry, dict):
                    continue

                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message")
                if not isinstance(message, dict):
                    continue

                if message.get("role") != "assistant":
                    continue

                content = message.get("content")
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") != "tool_use":
                        continue

                    if block.get("name") != "Read":
                        continue

                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        continue

                    if tool_input.get("file_path") == conftest_path_str:
                        return True

            return False

        except OSError:
            return False

    def check_and_inject(self, conftest_info: ConftestInfo) -> str:
        path = conftest_info["path"]

        full_path = self.project_root / path
        if not full_path.exists():
            return ""

        if self._has_conftest_been_read(full_path):
            return ""

        warning_message = (
            f"ACTION REQUIRED: Use Read tool to read CONFTEST immediately. READ NOW.\n"
            f"You MUST read the following conftest file before proceeding with the test:\n"
            f"{full_path}\n\n"
        )

        return warning_message


class HookHandler:
    def __init__(self) -> None:
        self.test_detector = TestFileDetector()

    def handle(self) -> None:
        hook_filename = Path(__file__).stem.replace("_", "-")

        try:
            input_raw = sys.stdin.read()
            if not input_raw:
                print(f"[{hook_filename}] Skipping: No input provided")
                sys.exit(0)

            data: PostToolUseInput = orjson.loads(input_raw)
        except (orjson.JSONDecodeError, KeyError, TypeError):
            print(f"[{hook_filename}] Skipping: Invalid input format")
            sys.exit(0)

        tool_name = data["tool_name"]
        tool_input = data["tool_input"]

        if tool_name != "Read":
            print(f"[{hook_filename}] Skipping: Tool {tool_name} not relevant")
            sys.exit(0)

        file_path = tool_input.get("file_path", "")

        if not file_path:
            print(f"[{hook_filename}] Skipping: No file path provided")
            sys.exit(0)

        if Path(file_path).resolve() == Path(__file__).resolve():
            print(f"[{hook_filename}] Skipping: Self-reference detected")
            sys.exit(0)

        if not self.test_detector.is_test_file(file_path):
            print(f"[{hook_filename}] Skipping: Not a test file")
            sys.exit(0)

        cwd = data["cwd"]
        transcript_path = data["transcript_path"]
        finder = ConftestFinder(file_path, cwd)

        if not finder.project_root:
            print(f"[{hook_filename}] Skipping: No project root found")
            sys.exit(0)

        conftest_infos = finder.find_conftests()

        if not conftest_infos:
            print(f"[{hook_filename}] Success: No conftest files found")
            sys.exit(0)

        injector = ConftestInjector(finder.project_root, transcript_path)
        messages_to_inject: list[str] = []

        for conftest_info in conftest_infos:
            message = injector.check_and_inject(conftest_info)
            if message:
                messages_to_inject.append(message)

        if messages_to_inject:
            combined_message = "\n\n".join(messages_to_inject)
            print(combined_message, file=sys.stderr)
            sys.exit(2)

        print(f"[{hook_filename}] Success: All conftest files already in context")
        sys.exit(0)


def main() -> None:
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    handler = HookHandler()
    handler.handle()


if __name__ == "__main__":
    main()
