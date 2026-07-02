"""
Parse Claude JSONL session files and extract conversations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .types import ConversationEntry, SessionInfo, SessionStats, ToolExecution, SessionNotFoundError, SessionParseError


CLAUDE_PROJECTS_ROOT = Path(
    os.environ.get("CLAUDE_PROJECTS_ROOT")
    or os.environ.get("CLAUDE_PROJECT_ROOT")
    or (Path.home() / ".claude" / "projects")
).expanduser()


class ClaudeSessionParser:
    """Parse Claude JSONL session files."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or CLAUDE_PROJECTS_ROOT

    def resolve_session(self, work_dir: Path, session_path: Optional[Path] = None) -> Path:
        """
        Resolve the session file path.

        Priority:
        1. Explicit session_path
        2. sessions-index.json (Claude official index)
        3. Scan project directory for latest .jsonl
        4. Fallback: scan all projects (requires CLAUDE_ALLOW_ANY_PROJECT_SCAN=1)
        """
        if session_path and session_path.exists():
            return session_path

        # Try sessions-index.json
        index_session = self._resolve_from_index(work_dir)
        if index_session:
            return index_session

        # Scan project directory
        project_session = self._scan_project_dir(work_dir)
        if project_session:
            return project_session

        # Fallback: scan all projects
        if os.environ.get("CLAUDE_ALLOW_ANY_PROJECT_SCAN") == "1":
            any_session = self._scan_all_projects()
            if any_session:
                return any_session

        raise SessionNotFoundError(f"No session found for {work_dir}")

    def _resolve_from_index(self, work_dir: Path) -> Optional[Path]:
        """Resolve session from sessions-index.json."""
        index_path = self.root / "sessions-index.json"
        if not index_path.exists():
            return None

        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            sessions = data.get("sessions", [])
            if not sessions:
                return None

            # Filter by project path and exclude sidechains
            work_dir_str = str(work_dir.resolve())
            candidates = []
            for s in sessions:
                if s.get("isSidechain"):
                    continue
                proj = s.get("projectPath", "")
                if proj and work_dir_str.startswith(proj):
                    candidates.append(s)

            if not candidates:
                # Fall back to most recent session
                candidates = [s for s in sessions if not s.get("isSidechain")]

            if not candidates:
                return None

            # Sort by lastModified descending
            candidates.sort(key=lambda x: x.get("lastModified", 0), reverse=True)
            best = candidates[0]
            session_id = best.get("sessionId")
            if not session_id:
                return None

            # Find the session file
            return self._find_session_file(session_id, work_dir)
        except Exception:
            return None

    def _find_session_file(self, session_id: str, work_dir: Path) -> Optional[Path]:
        """Find session file by ID."""
        # Try project-specific directory first
        project_dir = self._get_project_dir(work_dir)
        if project_dir:
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

        # Scan all project directories
        for proj_dir in self.root.iterdir():
            if not proj_dir.is_dir():
                continue
            candidate = proj_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate

        return None

    def _get_project_dir(self, work_dir: Path) -> Optional[Path]:
        """Get the Claude project directory for a work directory."""
        import re
        key = re.sub(r"[^A-Za-z0-9]", "-", str(work_dir.resolve()))
        proj_dir = self.root / key
        if proj_dir.exists():
            return proj_dir
        return None

    def _scan_project_dir(self, work_dir: Path) -> Optional[Path]:
        """Scan project directory for latest .jsonl file."""
        proj_dir = self._get_project_dir(work_dir)
        if not proj_dir or not proj_dir.exists():
            return None

        jsonl_files = list(proj_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None

        # Sort by modification time, newest first
        jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return jsonl_files[0]

    def _scan_all_projects(self) -> Optional[Path]:
        """Scan all project directories for latest session."""
        if not self.root.exists():
            return None

        best: Optional[Path] = None
        best_mtime = 0.0

        for proj_dir in self.root.iterdir():
            if not proj_dir.is_dir():
                continue
            for jsonl in proj_dir.glob("*.jsonl"):
                try:
                    mtime = jsonl.stat().st_mtime
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best = jsonl
                except Exception:
                    continue

        return best

    def parse_session(self, session_path: Path) -> list[ConversationEntry]:
        """Parse a session JSONL file into conversation entries."""
        if not session_path.exists():
            raise SessionNotFoundError(f"Session file not found: {session_path}")

        entries: list[ConversationEntry] = []
        errors = 0
        total = 0

        try:
            content = session_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise SessionParseError(f"Failed to read session file: {e}")

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                obj = json.loads(line)
                entry = self._parse_entry(obj)
                if entry:
                    entries.append(entry)
            except json.JSONDecodeError:
                errors += 1
                continue

        if total > 0 and errors / total > 0.5:
            raise SessionParseError(
                f"Too many parse errors: {errors}/{total} lines failed"
            )

        return entries

    def _parse_entry(self, obj: dict) -> Optional[ConversationEntry]:
        """Parse a single JSONL entry into a ConversationEntry."""
        if not isinstance(obj, dict):
            return None

        msg_type = obj.get("type")

        # Handle user messages
        if msg_type == "user":
            content = self._extract_content(obj.get("message", {}))
            if content:
                return ConversationEntry(
                    role="user",
                    content=content,
                    uuid=obj.get("uuid"),
                    parent_uuid=obj.get("parentUuid"),
                    timestamp=obj.get("timestamp"),
                )

        # Handle assistant messages
        if msg_type == "assistant":
            message = obj.get("message", {})
            content = self._extract_content(message)
            tool_calls = self._extract_tool_calls(message)
            if content or tool_calls:
                return ConversationEntry(
                    role="assistant",
                    content=content,
                    uuid=obj.get("uuid"),
                    parent_uuid=obj.get("parentUuid"),
                    timestamp=obj.get("timestamp"),
                    tool_calls=tool_calls,
                )

        return None

    def _extract_content(self, message: dict) -> str:
        """Extract text content from a message."""
        if not isinstance(message, dict):
            return ""

        # Direct content field
        content = message.get("content")
        if isinstance(content, str):
            return content

        # Content blocks
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts)

        return ""

    def _extract_tool_calls(self, message: dict) -> list[dict]:
        """Extract tool calls from a message."""
        if not isinstance(message, dict):
            return []

        content = message.get("content")
        if not isinstance(content, list):
            return []

        tool_calls = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })

        return tool_calls

    def get_session_info(self, session_path: Path) -> SessionInfo:
        """Get information about a session."""
        return SessionInfo(
            session_id=session_path.stem,
            session_path=str(session_path),
            last_modified=session_path.stat().st_mtime if session_path.exists() else None,
        )

    def extract_session_stats(self, session_path: Path) -> SessionStats:
        """Extract statistics from a session file."""
        if not session_path.exists():
            raise SessionNotFoundError(f"Session file not found: {session_path}")

        stats = SessionStats()
        seen_files: set[str] = set()
        # 收集 tool_use 和 tool_result 用于配对
        tool_uses: dict[str, dict] = {}  # tool_id -> {name, input}
        tool_results: dict[str, dict] = {}  # tool_id -> {content, is_error}

        try:
            content = session_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise SessionParseError(f"Failed to read session file: {e}")

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                self._collect_stats(obj, stats, seen_files, tool_uses, tool_results)
            except json.JSONDecodeError:
                continue

        # 配对 tool_use 和 tool_result 生成 ToolExecution
        self._build_tool_executions(stats, tool_uses, tool_results)

        return stats

    def _collect_stats(
        self,
        obj: dict,
        stats: SessionStats,
        seen_files: set[str],
        tool_uses: dict[str, dict],
        tool_results: dict[str, dict],
    ) -> None:
        """Collect statistics from a single JSONL entry."""
        if not isinstance(obj, dict):
            return

        msg_type = obj.get("type")
        message = obj.get("message", {})
        content = message.get("content", [])

        if not isinstance(content, list):
            content = []

        # Extract tool_use from assistant messages
        if msg_type == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    name = block.get("name", "unknown")
                    inp = block.get("input", {})

                    stats.tool_calls[name] = stats.tool_calls.get(name, 0) + 1
                    tool_uses[tool_id] = {"name": name, "input": inp}
                    self._extract_file_info(name, inp, stats, seen_files)

        # Extract tool_result from user messages
        if msg_type == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    result_content = block.get("content", "")
                    # 截断过长的结果
                    if isinstance(result_content, str) and len(result_content) > 2000:
                        result_content = result_content[:2000] + "...[truncated]"
                    tool_results[tool_id] = {
                        "content": result_content,
                        "is_error": block.get("is_error", False),
                    }

        # Extract from file-history-snapshot
        if msg_type == "file-history-snapshot":
            snapshot = obj.get("snapshot", {})
            backups = snapshot.get("trackedFileBackups", {})
            for path in backups.keys():
                if path not in seen_files:
                    stats.files_written.append(path)
                    seen_files.add(path)

    def _extract_file_info(
        self, tool_name: str, inp: dict, stats: SessionStats, seen_files: set[str]
    ) -> None:
        """Extract file information from tool input."""
        if not isinstance(inp, dict):
            return

        file_path = inp.get("file_path") or inp.get("path")

        if tool_name == "Write":
            if file_path and file_path not in seen_files:
                stats.files_written.append(file_path)
                seen_files.add(file_path)
        elif tool_name == "Read":
            if file_path and file_path not in seen_files:
                stats.files_read.append(file_path)
                seen_files.add(file_path)
        elif tool_name == "Edit":
            if file_path and file_path not in seen_files:
                stats.files_edited.append(file_path)
                seen_files.add(file_path)
        elif tool_name == "Bash":
            cmd = inp.get("command", "")
            if cmd and len(stats.bash_commands) < 20:
                # Truncate long commands
                if len(cmd) > 100:
                    cmd = cmd[:100] + "..."
                stats.bash_commands.append(cmd)
        elif tool_name == "TaskCreate":
            stats.tasks_created += 1
        elif tool_name == "TaskUpdate":
            status = inp.get("status", "")
            if status == "completed":
                stats.tasks_completed += 1

    def _build_tool_executions(
        self,
        stats: SessionStats,
        tool_uses: dict[str, dict],
        tool_results: dict[str, dict],
    ) -> None:
        """Build ToolExecution list by pairing tool_use and tool_result."""
        for tool_id, use in tool_uses.items():
            result = tool_results.get(tool_id, {})
            execution = ToolExecution(
                tool_id=tool_id,
                name=use.get("name", "unknown"),
                input=use.get("input", {}),
                result=result.get("content"),
                is_error=result.get("is_error", False),
            )
            stats.tool_executions.append(execution)
