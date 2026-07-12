"""
Grok (xAI Grok Build TUI) communication module.

Reads replies from ~/.grok/sessions/<encoded-cwd>/<session-id>/chat_history.jsonl
and sends messages by injecting text into the Grok TUI pane via the configured terminal backend.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import quote

from ccb_config import apply_backend_env
from ccb_protocol import REQ_ID_PREFIX
from project_id import compute_ccb_project_id
from session_utils import find_project_session_file, safe_write_session
from terminal import get_backend_for_session

apply_backend_env()

_REQ_ID_RE = re.compile(
    rf"{re.escape(REQ_ID_PREFIX)}\s*([0-9a-fA-F]{{32}}|\d{{8}}-\d{{6}}-\d{{3}}-\d+-\d+)"
)


def _default_grok_home() -> Path:
    override = (os.environ.get("GROK_HOME") or os.environ.get("XAI_GROK_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".grok"


def _encode_cwd_key(work_dir: Path) -> str:
    """Match Grok's session directory naming: quote full path with backslashes on Windows."""
    try:
        raw = str(work_dir.expanduser())
    except Exception:
        raw = str(work_dir)
    # Grok stores keys using the path as provided (Windows uses backslash).
    return quote(raw, safe="")


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
        normalized = str(value or "")
    normalized = normalized.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        normalized = normalized.lower()
    return normalized


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("text"), str):
                    parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
    return ""


def _sessions_root() -> Path:
    return _default_grok_home() / "sessions"


def _project_sessions_dir(work_dir: Path) -> Path:
    return _sessions_root() / _encode_cwd_key(work_dir)


def _active_session_id_for_cwd(work_dir: Path) -> Optional[str]:
    """Read ~/.grok/active_sessions.json and pick session matching work_dir."""
    path = _default_grok_home() / "active_sessions.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    target = _normalize_path_for_match(str(work_dir))
    exact: list[tuple[str, str]] = []  # (opened_at, session_id)
    for item in data:
        if not isinstance(item, dict):
            continue
        cwd = str(item.get("cwd") or "").strip()
        sid = str(item.get("session_id") or "").strip()
        if not sid:
            continue
        if cwd and _normalize_path_for_match(cwd) == target:
            exact.append((str(item.get("opened_at") or ""), sid))
    if exact:
        exact.sort(key=lambda x: x[0])
        return exact[-1][1]
    # Fallback: last entry in file
    for item in reversed(data):
        if isinstance(item, dict):
            sid = str(item.get("session_id") or "").strip()
            if sid:
                return sid
    return None


def resolve_grok_session_dir(work_dir: Path, session_id: str | None = None) -> Optional[Path]:
    """Locate the Grok session directory for a project."""
    # Prefer binding from project .grok-session
    session_file = find_project_session_file(work_dir, ".grok-session")
    bound_id = (session_id or "").strip()
    bound_path: Optional[Path] = None
    if session_file and session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                if not bound_id:
                    bound_id = str(data.get("grok_session_id") or "").strip()
                raw_path = str(data.get("grok_session_path") or "").strip()
                if raw_path:
                    p = Path(raw_path).expanduser()
                    if p.exists():
                        bound_path = p if p.is_dir() else p.parent
        except Exception:
            pass

    if bound_path and bound_path.exists():
        return bound_path

    project_dir = _project_sessions_dir(work_dir)
    if not project_dir.exists():
        # Try alternate encodings (forward-slash form)
        alt = _sessions_root() / quote(str(work_dir).replace("\\", "/"), safe="")
        if alt.exists():
            project_dir = alt
        else:
            return None

    sid = bound_id or _active_session_id_for_cwd(work_dir)
    if sid:
        candidate = project_dir / sid
        if candidate.is_dir():
            return candidate

    # Latest session subdirectory by mtime
    try:
        dirs = [p for p in project_dir.iterdir() if p.is_dir()]
    except Exception:
        dirs = []
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return dirs[0]


class GrokLogReader:
    """Read Grok chat_history.jsonl and extract conversation events."""

    def __init__(self, work_dir: Path, session_id_filter: str | None = None):
        self.work_dir = work_dir
        self.session_id_filter = session_id_filter

    def session_dir(self) -> Path | None:
        return resolve_grok_session_dir(self.work_dir, self.session_id_filter)

    def chat_history_path(self) -> Path | None:
        session_dir = self.session_dir()
        if not session_dir:
            return None
        path = session_dir / "chat_history.jsonl"
        return path if path.exists() else path  # may not exist yet; still return expected path

    def current_session_id(self) -> str | None:
        session_dir = self.session_dir()
        if not session_dir:
            return None
        return session_dir.name

    def latest_conversations(self, n: int = 5) -> list[tuple[str, str]]:
        path = self.chat_history_path()
        if not path or not path.exists():
            return []

        pairs: list[tuple[str, str]] = []
        current_user: str | None = None
        assistant_chunks: list[str] = []

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    role = (entry.get("type") or entry.get("role") or "").strip().lower()
                    text = _extract_text(entry.get("content")).strip()
                    if role == "user" and text:
                        if current_user is not None and assistant_chunks:
                            pairs.append((current_user, "\n".join(assistant_chunks).strip()))
                            assistant_chunks = []
                        current_user = text
                    elif role == "assistant" and text:
                        assistant_chunks.append(text)
            if current_user is not None and assistant_chunks:
                pairs.append((current_user, "\n".join(assistant_chunks).strip()))
        except Exception:
            pass

        return pairs[-n:] if n > 0 else pairs

    def capture_state(self) -> dict:
        log = self.chat_history_path()
        offset = -1
        if log and log.exists():
            try:
                offset = log.stat().st_size
            except OSError:
                offset = 0
        return {"log_path": log, "offset": offset}

    def _extract_event(self, entry: dict) -> tuple[str, str] | None:
        role = (entry.get("type") or entry.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            return None
        text = _extract_text(entry.get("content")).strip()
        if not text:
            return None
        return role, text

    def wait_for_event(self, state: dict, timeout: float) -> tuple[tuple[str, str] | None, dict]:
        """
        Poll for new chat_history events since last state.
        Returns ((role, text), new_state) or (None, state) on timeout.
        """
        deadline = time.time() + timeout
        log_path = state.get("log_path")
        offset = state.get("offset", -1)
        if not isinstance(offset, int):
            offset = -1

        while True:
            latest = self.chat_history_path()
            if latest and (not log_path or str(latest) != str(log_path)):
                log_path = latest
                offset = 0

            if not log_path or not Path(log_path).exists():
                if time.time() >= deadline:
                    return None, {"log_path": log_path, "offset": offset}
                time.sleep(0.4)
                continue

            log_path = Path(log_path)
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
                    fh.seek(offset, os.SEEK_SET)
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
                        if not isinstance(entry, dict):
                            continue
                        event = self._extract_event(entry)
                        if event is not None:
                            return event, {"log_path": log_path, "offset": offset}
            except OSError:
                pass

            if time.time() >= deadline:
                return None, {"log_path": log_path, "offset": offset}
            time.sleep(0.4)


class GrokSessionManager:
    """Manage Grok session file binding and pane resolution."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir

    def _session_file_path(self) -> Path:
        return self.work_dir / ".ccb" / ".grok-session"

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
        data = self._read_session()
        if not data:
            reader = GrokLogReader(self.work_dir)
            sid = reader.current_session_id()
            sdir = reader.session_dir()
            data = {
                "session_id": f"ccb-grok-{int(time.time())}",
                "ccb_session_id": f"ccb-grok-{int(time.time())}",
                "ccb_project_id": compute_ccb_project_id(self.work_dir),
                "work_dir": str(self.work_dir),
                "terminal": "wezterm",
                "active": True,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pane_title_marker": "CCB-Grok",
                "grok_session_id": sid,
                "grok_session_path": str(sdir) if sdir else None,
            }
            # Bind current WezTerm pane if running inside Grok
            pane = (os.environ.get("WEZTERM_PANE") or os.environ.get("TMUX_PANE") or "").strip()
            if pane:
                data["pane_id"] = pane
            if os.environ.get("GROK_AGENT"):
                data["terminal"] = "wezterm" if os.environ.get("WEZTERM_PANE") else data["terminal"]
            self._write_session(data)
        return data

    def get_pane_id(self) -> str | None:
        data = self._read_session()
        return data.get("pane_id")

    def update_pane_id(self, pane_id: str) -> None:
        data = self._read_session()
        data["pane_id"] = pane_id
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_session(data)

    def send_to_pane(self, text: str) -> bool:
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


class GrokCommunicator:
    """High-level Grok connectivity helper for ccb-ping / diagnostics."""

    def __init__(self, work_dir: Path | None = None):
        self.work_dir = work_dir or Path.cwd()

    def ping(self, display: bool = True) -> Tuple[bool, str]:
        session_file = find_project_session_file(self.work_dir, ".grok-session")
        if not session_file:
            msg = "No .grok-session for this project. Run `ccb grok` first (or bind current session)."
            if display:
                print(msg)
            return False, msg

        try:
            data = json.loads(session_file.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            msg = f"Failed to read .grok-session: {exc}"
            if display:
                print(msg)
            return False, msg

        pane_id = str(data.get("pane_id") or "").strip()
        backend = get_backend_for_session(data)
        pane_ok = False
        if backend and pane_id:
            try:
                pane_ok = bool(backend.is_alive(pane_id))
            except Exception:
                pane_ok = False

        reader = GrokLogReader(self.work_dir, session_id_filter=str(data.get("grok_session_id") or "") or None)
        sdir = reader.session_dir()
        history = reader.chat_history_path()
        history_ok = bool(history and history.exists())

        if pane_ok and history_ok:
            msg = f"Grok OK pane={pane_id} session={sdir.name if sdir else '?'} history={history}"
            if display:
                print(msg)
            return True, msg

        parts = []
        if not pane_ok:
            parts.append(f"pane not alive ({pane_id or 'missing'})")
        if not history_ok:
            parts.append("chat_history.jsonl not found")
        msg = "Grok degraded: " + "; ".join(parts)
        if display:
            print(msg)
        # Pane-alive is the critical signal for messaging; history may lag on brand-new sessions.
        return pane_ok, msg
