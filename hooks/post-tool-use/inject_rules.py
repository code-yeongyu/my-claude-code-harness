#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "orjson",
#     "pyyaml",
#     "aiofiles",
#     "wcmatch",
# ]
# ///
# pyright: reportMissingImports=false, reportMissingModuleSource=false

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

import aiofiles
import orjson
import yaml
from wcmatch import glob


class WriteToolInput(TypedDict):
    file_path: str
    content: str


class EditToolInput(TypedDict):
    file_path: str
    old_string: str
    new_string: str


class MultiEditToolInput(TypedDict):
    file_path: str
    edits: list[dict[str, str]]


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


class ToolUseBlock(TypedDict):
    type: Literal["tool_use"]
    name: str
    id: str
    input: dict[str, Any]


class RuleMetadata(TypedDict):
    description: NotRequired[str]
    globs: NotRequired[list[str]]
    alwaysApply: NotRequired[bool]


class RuleInfo(TypedDict):
    path: Path
    relative_path: str
    distance: int
    content: str
    content_hash: str
    metadata: RuleMetadata
    match_reason: str


class RuleFinder:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.project_root = self._find_project_root()
        self.user_rules_dir = Path.home() / ".claude" / "modular-prompts"

    def _find_project_root(self) -> Path:
        markers = [".git", "pyproject.toml", "package.json", ".venv"]
        current = self.cwd

        while current != current.parent:
            for marker in markers:
                if (current / marker).exists():
                    return current
            current = current.parent

        return self.cwd

    def find_rule_files(self, current_file_path: Path) -> list[tuple[Path, int]]:
        candidates: list[tuple[Path, int]] = []

        project_rule_dirs = [
            self.project_root / ".cursor" / "rules",
            self.project_root / ".claude" / "modular-prompts",
        ]

        for rule_dir in project_rule_dirs:
            if not rule_dir.exists():
                continue

            for pattern in ["**/*.mdc", "**/*.md"]:
                for rule_file in rule_dir.glob(pattern):
                    if rule_file.is_file():
                        distance = self._calculate_distance(rule_file, current_file_path)
                        candidates.append((rule_file, distance))

        if self.user_rules_dir.exists():
            for pattern in ["**/*.mdc", "**/*.md"]:
                for rule_file in self.user_rules_dir.glob(pattern):
                    if rule_file.is_file():
                        candidates.append((rule_file, 9999))

        candidates.sort(key=lambda x: x[1])

        return candidates

    def _calculate_distance(self, rule_file: Path, current_file: Path) -> int:
        try:
            rule_dir = rule_file.parent
            current_dir = current_file.parent

            rule_rel = rule_dir.relative_to(self.project_root)
            current_rel = current_dir.relative_to(self.project_root)

            rule_parts = rule_rel.parts
            current_parts = current_rel.parts

            common = 0
            for r, c in zip(rule_parts, current_parts):
                if r == c:
                    common += 1
                else:
                    break

            distance = len(current_parts) - common

            return distance

        except ValueError:
            return 9999


class RuleParser:
    @staticmethod
    def parse_frontmatter(content: str) -> tuple[RuleMetadata, str]:
        pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(pattern, content, re.DOTALL)

        if not match:
            return RuleMetadata(), content

        yaml_str = match.group(1)
        markdown = match.group(2)

        try:
            metadata = yaml.safe_load(yaml_str) or {}
        except yaml.YAMLError:
            metadata = {}

        if "globs" in metadata and isinstance(metadata["globs"], str):
            metadata["globs"] = [g.strip() for g in metadata["globs"].split(",") if g.strip()]

        return RuleMetadata(**metadata), markdown


class RuleMatcher:
    @staticmethod
    def should_apply(rule_metadata: RuleMetadata, current_file_path: Path, cwd: Path) -> str | None:
        if rule_metadata.get("alwaysApply"):
            return "alwaysApply"

        globs = rule_metadata.get("globs", [])
        if not globs:
            return None

        try:
            relative_path = current_file_path.relative_to(cwd)
        except ValueError:
            return None

        relative_path_str = str(relative_path)
        for glob_pattern in globs:
            if glob.globmatch(relative_path_str, glob_pattern, flags=glob.GLOBSTAR):
                return f"glob: {glob_pattern}"

        return None


class TranscriptAnalyzer:
    def __init__(self, transcript_path: Path) -> None:
        self.transcript_path = transcript_path
        self._read_contents_cache: tuple[dict[str, str], set[str]] | None = None

    async def get_already_read_rule_contents(self) -> tuple[dict[str, str], set[str]]:
        """
        Returns:
            - dict[realpath_str, content_hash]: Map of resolved paths to their content hashes
            - set[content_hash]: Set of all content hashes that have been read
        """
        if self._read_contents_cache is not None:
            return self._read_contents_cache

        if not self.transcript_path.exists():
            self._read_contents_cache = ({}, set())
            return ({}, set())

        read_files: dict[str, str] = {}
        read_hashes: set[str] = set()

        try:
            async with aiofiles.open(self.transcript_path, encoding="utf-8") as f:
                content = await f.read()

            for line in content.splitlines():
                if not line.strip():
                    continue

                try:
                    entry = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue

                message = entry.get("message", {})
                if message.get("role") != "assistant":
                    continue

                for block in message.get("content", []):
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") != "tool_use" or block.get("name") != "Read":
                        continue

                    tool_input = block.get("input", {})
                    file_path = tool_input.get("file_path")

                    if not file_path:
                        continue

                    if not (file_path.endswith(".md") or file_path.endswith(".mdc")):
                        continue

                    try:
                        path = Path(file_path)
                        if path.exists():
                            real_path = path.resolve()
                            content_hash = await self._hash_file_content(real_path)
                            read_files[str(real_path)] = content_hash
                            read_hashes.add(content_hash)
                    except OSError:
                        pass

        except OSError:
            pass

        self._read_contents_cache = (read_files, read_hashes)
        return (read_files, read_hashes)

    @staticmethod
    async def _hash_file_content(file_path: Path) -> str:
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()

            _, markdown = RuleParser.parse_frontmatter(content)
            stripped = markdown.strip()
            return hashlib.sha256(stripped.encode("utf-8")).hexdigest()[:16]
        except OSError:
            return "error"


async def process_single_rule_file(
    rule_path: Path,
    distance: int,
    current_file_path: Path,
    cwd: Path,
    already_read_contents: tuple[dict[str, str], set[str]],
) -> RuleInfo | None:
    try:
        async with aiofiles.open(rule_path, encoding="utf-8") as f:
            content = await f.read()

        metadata, markdown = RuleParser.parse_frontmatter(content)

        matcher = RuleMatcher()
        match_reason = matcher.should_apply(metadata, current_file_path, cwd)

        if not match_reason:
            return None

        content_hash = hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()[:16]

        already_read_paths, already_read_hashes = already_read_contents

        real_rule_path = rule_path.resolve()
        real_rule_path_str = str(real_rule_path)

        if real_rule_path_str in already_read_paths:
            if already_read_paths[real_rule_path_str] == content_hash:
                return None

        if content_hash in already_read_hashes:
            return None

        try:
            relative_path = str(rule_path.relative_to(cwd))
        except ValueError:
            relative_path = str(rule_path)

        return RuleInfo(
            path=rule_path,
            relative_path=relative_path,
            distance=distance,
            content=content,
            content_hash=content_hash,
            metadata=metadata,
            match_reason=match_reason,
        )

    except Exception:
        return None


class RuleInjector:
    TODOS_DIR = Path.home() / ".claude" / "todos"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def _generate_todo_id(self, rule_info: RuleInfo) -> str:
        safe_path = rule_info["relative_path"].replace("/", "-").replace(".", "-")
        return f"read-rule-{safe_path}"

    def _generate_todo_id_from_path(self, rule_path: Path, project_root: Path) -> str:
        try:
            relative_path = str(rule_path.relative_to(project_root))
        except ValueError:
            relative_path = str(rule_path)
        safe_path = relative_path.replace("/", "-").replace(".", "-")
        return f"read-rule-{safe_path}"

    def _mark_todo_completed(self, todo_id: str) -> None:
        self.TODOS_DIR.mkdir(parents=True, exist_ok=True)
        todo_file = self.TODOS_DIR / f"{self.session_id}-agent-{self.session_id}.json"

        if not todo_file.exists():
            return

        try:
            todos = orjson.loads(todo_file.read_bytes())
        except (orjson.JSONDecodeError, OSError):
            return

        for todo in todos:
            if todo.get("id") == todo_id:
                if todo.get("status") != "completed":
                    todo["status"] = "completed"
                break

        try:
            todo_file.write_bytes(orjson.dumps(todos, option=orjson.OPT_INDENT_2))
        except OSError:
            pass

    def add_todo_item(self, rule_info: RuleInfo) -> None:
        self.TODOS_DIR.mkdir(parents=True, exist_ok=True)
        todo_file = self.TODOS_DIR / f"{self.session_id}-agent-{self.session_id}.json"

        try:
            if todo_file.exists():
                todos = orjson.loads(todo_file.read_bytes())
            else:
                todos = []
        except (orjson.JSONDecodeError, OSError):
            todos = []

        todo_id = self._generate_todo_id(rule_info)

        for existing in todos:
            if existing.get("id") == todo_id:
                return

        todo_item = {
            "content": f"Read rule: {rule_info['path']} ({rule_info['match_reason']})",
            "status": "pending",
            "priority": "high",
            "id": todo_id,
        }

        todos.insert(0, todo_item)

        try:
            todo_file.write_bytes(orjson.dumps(todos, option=orjson.OPT_INDENT_2))
        except OSError:
            pass


class HookHandler:
    async def handle(self) -> None:
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

        if tool_name not in ["Read", "Write", "Edit", "MultiEdit", "NotebookEdit"]:
            print(f"[{hook_filename}] Skipping: Tool {tool_name} not relevant")
            sys.exit(0)

        file_path = ""
        if "file_path" in tool_input:
            file_path = tool_input["file_path"]  # type: ignore
        elif "notebook_path" in tool_input:
            file_path = tool_input["notebook_path"]  # type: ignore

        if not file_path:
            print(f"[{hook_filename}] Skipping: No file path provided")
            sys.exit(0)

        if Path(file_path).resolve() == Path(__file__).resolve():
            print(f"[{hook_filename}] Skipping: Self-reference detected")
            sys.exit(0)

        current_file_path = Path(file_path).resolve()
        cwd = Path(data["cwd"])
        session_id = data["session_id"]

        if tool_name == "Read" and current_file_path.suffix in [".md", ".mdc"]:
            rule_dirs = [
                cwd / ".cursor" / "rules",
                cwd / ".claude" / "modular-prompts",
                Path.home() / ".claude" / "modular-prompts",
            ]

            for rule_dir in rule_dirs:
                if not rule_dir.exists():
                    continue

                try:
                    current_file_path.relative_to(rule_dir)
                    finder = RuleFinder(cwd)
                    injector = RuleInjector(session_id)
                    todo_id = injector._generate_todo_id_from_path(current_file_path, finder.project_root)
                    injector._mark_todo_completed(todo_id)

                    print(f"[{hook_filename}] Success: Rule todo completed")
                    sys.exit(0)
                except ValueError:
                    continue

        transcript_path = Path(data["transcript_path"])
        finder = RuleFinder(cwd)
        rule_candidates = finder.find_rule_files(current_file_path)

        if not rule_candidates:
            print(f"[{hook_filename}] Success: No rule files found")
            sys.exit(0)

        analyzer = TranscriptAnalyzer(transcript_path)
        already_read = await analyzer.get_already_read_rule_contents()

        tasks = [
            process_single_rule_file(rule_path, distance, current_file_path, cwd, already_read)
            for rule_path, distance in rule_candidates
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        rules_to_inject: list[RuleInfo] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is None:
                continue
            rules_to_inject.append(result)  # pyright: ignore[reportArgumentType]

        if not rules_to_inject:
            print(f"[{hook_filename}] Success: All rules already in context")
            sys.exit(0)

        injector = RuleInjector(session_id)
        messages: list[str] = []

        for rule_info in rules_to_inject:
            injector.add_todo_item(rule_info)

            message = (
                f"FUCKING READ THIS RIGHT NOW! ACTION REQUIRED IMMEDIATELY!\n"
                f"A critical rule applies to the current file you're working with.\n"
                f"You MUST READ this rule file IMMEDIATELY before proceeding with ANY operations.\n"
                f"DO NOT assume it's irrelevant. READ IT NOW WITH Read TOOL:\n"
                f"{rule_info['path']}\n"
                f"Reason: {rule_info['match_reason']}\n\n"
                f"[Todo added to ~/.claude/todos/{session_id}-agent-{session_id}.json]"
            )
            messages.append(message)

        combined_message = "\n\n".join(messages)
        print(combined_message, file=sys.stderr)
        sys.exit(2)


async def main() -> None:
    hook_filename = Path(__file__).stem.replace("_", "-")
    print(f"\n[{hook_filename}]", file=sys.stderr)

    handler = HookHandler()
    await handler.handle()


if __name__ == "__main__":
    asyncio.run(main())
