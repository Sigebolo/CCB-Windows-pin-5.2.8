"""
MiMo communication module

Reads replies from MiMo storage and sends messages by
injecting text into the MiMo TUI pane via the configured terminal backend.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ccb_protocol import REQ_ID_PREFIX
from ccb_config import apply_backend_env
from i18n import t
from terminal import get_backend_for_session, get_pane_id_from_session
from session_utils import find_project_session_file, safe_write_session
from session_file_watcher import SessionFileWatcher, HAS_WATCHDOG
from pane_registry import upsert_registry
from project_id import compute_ccb_project_id

apply_backend_env()

_REQ_ID_RE = re.compile(rf"{re.escape(REQ_ID_PREFIX)}\s*([0-9a-fA-F]{{32}}|\d{{8}}-\d{{6}}-\d{{3}}-\d+-\d+)")


def compute_mimo_project_id(work_dir: Path) -> str:
    """Compute MiMo project ID for a directory."""
    try:
        cwd = Path(work_dir).expanduser()
    except Exception:
        cwd = Path.cwd()

    def _find_git_dir(start: Path) -> tuple[Path | None, Path | None]:
        for candidate in [start, *start.parents]:
            git_entry = candidate / ".git"
            if not git_entry.exists():
                continue
            if git_entry.is_dir():
                return candidate, git_entry
            if git_entry.is_file():
                try:
                    raw = git_entry.read_text(encoding="utf-8", errors="replace").strip()
                    prefix = "gitdir:"
                    if raw.lower().startswith(prefix):
                        gitdir = raw[len(prefix):].strip()
                        gitdir_path = Path(gitdir)
                        if not gitdir_path.is_absolute():
                            gitdir_path = (candidate / gitdir_path).resolve()
                        return candidate, gitdir_path
                except Exception:
                    continue
        return None, None

    git_root, git_dir = _find_git_dir(cwd)

    try:
        if not shutil.which("git"):
            return "global"

        kwargs = {
            "cwd": str(git_root or cwd),
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "check": False,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = startupinfo
        proc = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "--all"],
            **kwargs
        )
        roots = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        roots.sort()
        return roots[0] if roots else "global"
    except Exception:
        return "global"


def _normalize_path_for_match(value: str) -> str:
    s = (value or "").strip()
    if os.name == "nt":
        if len(s) >= 4 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
            s = f"{s[1].lower()}:/{s[3:]}"
        m = re.match(r"^/mnt/([A-Za-z])/(.*)$", s)
        if m:
            s = f"{m.group(1).lower()}:/{m.group(2)}"
    try:
        path = Path(s).expanduser()
        normalized = str(path.absolute())
    except Exception:
        normalized = str(value)
    normalized = normalized.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        normalized = normalized.lower()
    return normalized


class MiMoLogReader:
    """Read MiMo session logs and extract conversations."""

    def __init__(self, work_dir: Path, session_id_filter: str | None = None):
        self.work_dir = work_dir
        self.session_id_filter = session_id_filter

    def _latest_session(self) -> Path | None:
        """Find the latest MiMo session file."""
        # MiMo stores sessions in ~/.mimocode/sessions/ or similar
        home = Path.home()
        candidates = [
            home / ".mimocode" / "sessions",
            home / ".config" / "mimocode" / "sessions",
        ]
        for base in candidates:
            if not base.exists():
                continue
            sessions = sorted(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if sessions:
                return sessions[0]
        return None

    def latest_conversations(self, n: int = 5) -> list[tuple[str, str]]:
        """Extract last N conversation pairs from session."""
        session_path = self._latest_session()
        if not session_path or not session_path.exists():
            return []

        pairs: list[tuple[str, str]] = []
        current_user: str | None = None

        try:
            with open(session_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    role = entry.get("role", "")
                    content = entry.get("content", "")

                    if role == "user":
                        current_user = content
                    elif role == "assistant" and current_user:
                        pairs.append((current_user, content))
                        current_user = None
                        if len(pairs) >= n:
                            break
        except Exception:
            pass

        return pairs


class MiMoSessionManager:
    """Manage MiMo session file binding and pane resolution."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self._watcher: SessionFileWatcher | None = None

    def _session_file_path(self) -> Path:
        return self.work_dir / ".ccb" / ".mimo-session"

    def _read_session(self) -> dict:
        path = self._session_file_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _write_session(self, data: dict) -> None:
        path = self._session_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        safe_write_session(path, payload)

    def ensure_session(self) -> dict:
        """Ensure session file exists and is active."""
        data = self._read_session()
        if not data:
            data = {
                "session_id": f"ccb-mimo-{int(time.time())}",
                "ccb_project_id": compute_ccb_project_id(self.work_dir),
                "work_dir": str(self.work_dir),
                "terminal": "wezterm",
                "active": True,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pane_title_marker": "CCB-Mimo",
            }
            self._write_session(data)
        return data

    def get_pane_id(self) -> str | None:
        """Get the current pane ID for MiMo."""
        data = self._read_session()
        return data.get("pane_id")

    def update_pane_id(self, pane_id: str) -> None:
        """Update the pane ID."""
        data = self._read_session()
        data["pane_id"] = pane_id
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_session(data)

    def send_to_pane(self, text: str) -> bool:
        """Send text to MiMo pane."""
        pane_id = self.get_pane_id()
        if not pane_id:
            return False

        backend = get_backend_for_session(self._read_session())
        if not backend:
            return False

        try:
            backend.send_text(pane_id, text)
            return True
        except Exception:
            return False
