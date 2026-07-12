"""
Kiro communication module

Reads replies from Kiro storage and sends messages by
injecting text into the Kiro TUI pane via the configured terminal backend.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ccb_protocol import REQ_ID_PREFIX
from ccb_config import apply_backend_env
from i18n import t
from terminal import get_backend_for_session, get_pane_id_from_session
from session_utils import find_project_session_file, safe_write_session
from pane_registry import upsert_registry
from project_id import compute_ccb_project_id

apply_backend_env()

_REQ_ID_RE = re.compile(rf"{re.escape(REQ_ID_PREFIX)}\s*([0-9a-fA-F]{{32}}|\d{{8}}-\d{{6}}-\d{{3}}-\d+-\d+)")


def compute_kiro_project_id(work_dir: Path) -> str:
    """Compute Kiro project ID for a directory."""
    return compute_ccb_project_id(work_dir)


class KiroLogReader:
    """Read Kiro session logs and extract conversations."""

    def __init__(self, work_dir: Path, session_id_filter: str | None = None):
        self.work_dir = work_dir
        self.session_id_filter = session_id_filter

    def _latest_session(self) -> Path | None:
        """Find the latest Kiro session file."""
        home = Path.home()
        candidates = [
            home / ".kiro" / "sessions",
            home / ".config" / "kiro" / "sessions",
            home / "AppData" / "Local" / "kiro" / "sessions",
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

    def capture_state(self) -> dict:
        """Capture current log path and read offset for incremental polling."""
        log = self._latest_session()
        offset = -1
        if log and log.exists():
            try:
                offset = log.stat().st_size
            except OSError:
                offset = 0
        return {"log_path": log, "offset": offset}

    def _extract_event(self, entry: dict) -> tuple[str, str] | None:
        """Extract a (role, text) event from a JSONL entry."""
        role = (entry.get("role") or "").strip().lower()
        content = entry.get("content") or entry.get("text") or ""
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            return role, content.strip()
        return None

    def wait_for_event(self, state: dict, timeout: float) -> tuple[tuple[str, str] | None, dict]:
        """
        Poll for new events since last state. Returns ((role, text), new_state)
        or (None, state) on timeout.
        """
        import os as _os
        deadline = time.time() + timeout
        log_path = state.get("log_path")
        offset = state.get("offset", -1)
        if not isinstance(offset, int):
            offset = -1

        while True:
            latest = self._latest_session()
            if latest and (not log_path or str(latest) != str(log_path)):
                log_path = latest
                offset = 0

            if not log_path or not log_path.exists():
                if time.time() >= deadline:
                    return None, state
                time.sleep(0.5)
                continue

            try:
                size = log_path.stat().st_size
            except OSError:
                size = None

            if offset < 0:
                offset = size if isinstance(size, int) else 0

            try:
                with log_path.open("rb") as fh:
                    if isinstance(size, int) and offset > size:
                        offset = size
                    fh.seek(offset, _os.SEEK_SET)
                    while True:
                        if time.time() >= deadline:
                            return None, {"log_path": log_path, "offset": offset}
                        pos_before = fh.tell()
                        raw_line = fh.readline()
                        if not raw_line:
                            break
                        if not raw_line.endswith(b"\n"):
                            fh.seek(pos_before)
                            break
                        offset = fh.tell()
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        event = self._extract_event(entry)
                        if event is not None:
                            return event, {"log_path": log_path, "offset": offset}
            except OSError:
                pass

            if time.time() >= deadline:
                return None, {"log_path": log_path, "offset": offset}
            time.sleep(0.5)


class KiroSessionManager:
    """Manage Kiro session file binding and pane resolution."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir

    def _session_file_path(self) -> Path:
        return self.work_dir / ".ccb" / ".kiro-session"

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
                "session_id": f"ccb-kiro-{int(time.time())}",
                "ccb_project_id": compute_ccb_project_id(self.work_dir),
                "work_dir": str(self.work_dir),
                "terminal": "wezterm",
                "active": True,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pane_title_marker": "CCB-Kiro",
            }
            self._write_session(data)
        return data

    def get_pane_id(self) -> str | None:
        """Get the current pane ID for Kiro."""
        data = self._read_session()
        return data.get("pane_id")

    def update_pane_id(self, pane_id: str) -> None:
        """Update the pane ID."""
        data = self._read_session()
        data["pane_id"] = pane_id
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_session(data)

    def send_to_pane(self, text: str) -> bool:
        """Send text to Kiro pane."""
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
