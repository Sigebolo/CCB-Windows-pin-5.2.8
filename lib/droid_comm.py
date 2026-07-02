"""
Droid communication module.

Reads replies from ~/.factory/sessions/<slug>/<session-id>.jsonl and
sends prompts by injecting text into the Droid pane via the configured backend.
"""

from __future__ import annotations

import heapq
import json
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ccb_config import apply_backend_env
from pane_registry import upsert_registry
from project_id import compute_ccb_project_id
from session_utils import find_project_session_file, safe_write_session
from session_file_watcher import SessionFileWatcher, HAS_WATCHDOG
from terminal import get_backend_for_session, get_pane_id_from_session

apply_backend_env()


def _default_sessions_root() -> Path:
    override = (os.environ.get("DROID_SESSIONS_ROOT") or os.environ.get("FACTORY_SESSIONS_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()
    factory_home = (os.environ.get("FACTORY_HOME") or os.environ.get("FACTORY_ROOT") or "").strip()
    base = Path(factory_home).expanduser() if factory_home else (Path.home() / ".factory")
    return base / "sessions"


DROID_SESSIONS_ROOT = _default_sessions_root()

_DROID_WATCHER: Optional[SessionFileWatcher] = None
_DROID_WATCH_STARTED = False
_DROID_WATCH_LOCK = threading.Lock()


def _normalize_path_for_match(value: str) -> str:
    s = (value or "").strip()
    if os.name == "nt":
        if len(s) >= 4 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
            s = f"{s[1].lower()}:/{s[3:]}"
        if s.startswith("/mnt/") and len(s) > 6:
            drive = s[5]
            if drive.isalpha() and s[6:7] == "/":
                s = f"{drive.lower()}:/{s[7:]}"
    try:
        path = Path(s).expanduser()
        normalized = str(path.absolute())
    except Exception:
        normalized = str(value or "")
    normalized = normalized.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        normalized = normalized.lower()
    return normalized


def _path_is_same_or_parent(parent: str, child: str) -> bool:
    parent_norm = _normalize_path_for_match(parent)
    child_norm = _normalize_path_for_match(child)
    if not parent_norm or not child_norm:
        return False
    if parent_norm == child_norm:
        return True
    if not child_norm.startswith(parent_norm):
        return False
    return child_norm == parent_norm or child_norm[len(parent_norm) :].startswith("/")


def read_droid_session_start(session_path: Path, *, max_lines: int = 30) -> Tuple[Optional[str], Optional[str]]:
    """
    Best-effort read of the session_start line for (cwd, session_id).
    """
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(max_lines):
                line = handle.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "session_start":
                    continue
                cwd = entry.get("cwd")
                sid = entry.get("id")
                cwd_str = str(cwd).strip() if isinstance(cwd, str) else None
                sid_str = str(sid).strip() if isinstance(sid, str) else None
                return cwd_str or None, sid_str or None
    except OSError:
        return None, None
    return None, None


def _handle_droid_session_event(path: Path) -> None:
    if not path or not path.exists() or path.suffix != ".jsonl":
        return
    cwd, session_id = read_droid_session_start(path)
    if not cwd:
        return
    try:
        work_dir = Path(cwd).expanduser()
    except Exception:
        return
    session_file = find_project_session_file(work_dir, ".droid-session")
    if not session_file or not session_file.exists():
        return
    try:
        from daskd_session import load_project_session
    except Exception:
        return
    session = load_project_session(work_dir)
    if not session:
        return
    try:
        session.update_droid_binding(session_path=path, session_id=session_id)
    except Exception:
        return


def _ensure_droid_watchdog_started() -> None:
    if not HAS_WATCHDOG:
        return
    global _DROID_WATCHER, _DROID_WATCH_STARTED
    if _DROID_WATCH_STARTED:
        return
    with _DROID_WATCH_LOCK:
        if _DROID_WATCH_STARTED:
            return
        if not DROID_SESSIONS_ROOT.exists():
            return
        watcher = SessionFileWatcher(DROID_SESSIONS_ROOT, _handle_droid_session_event, recursive=True)
        try:
            watcher.start()
        except Exception:
            return
        _DROID_WATCHER = watcher
        _DROID_WATCH_STARTED = True


def _extract_content_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    texts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in ("thinking", "thinking_delta"):
            continue
        text = item.get("text")
        if not text and item_type == "text":
            text = item.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    if not texts:
        return None
    return "\n".join(texts).strip()


def _extract_message(entry: dict, role: str) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    entry_type = str(entry.get("type") or "").strip().lower()
    if entry_type == "message":
        message = entry.get("message")
        if isinstance(message, dict):
            msg_role = str(message.get("role") or "").strip().lower()
            if msg_role == role:
                return _extract_content_text(message.get("content"))
    msg_role = str(entry.get("role") or entry_type).strip().lower()
    if msg_role == role:
        return _extract_content_text(entry.get("content") or entry.get("message"))
    return None


class DroidLogReader:
    """Reads Droid session logs from ~/.factory/sessions"""

    def __init__(self, root: Path = DROID_SESSIONS_ROOT, work_dir: Optional[Path] = None):
        self.root = Path(root).expanduser()
        self.work_dir = work_dir or Path.cwd()
        self._preferred_session: Optional[Path] = None
        self._session_id_hint: Optional[str] = None
        try:
            poll = float(os.environ.get("DROID_POLL_INTERVAL", "0.05"))
        except Exception:
            poll = 0.05
        self._poll_interval = min(0.5, max(0.02, poll))
        try:
            limit = int(os.environ.get("DROID_SESSION_SCAN_LIMIT", "200"))
        except Exception:
            limit = 200
        self._scan_limit = max(1, limit)

    def set_preferred_session(self, session_path: Optional[Path]) -> None:
        if not session_path:
            return
        try:
            candidate = session_path if isinstance(session_path, Path) else Path(str(session_path)).expanduser()
        except Exception:
            return
        if candidate.exists():
            self._preferred_session = candidate

    def set_session_id_hint(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        self._session_id_hint = str(session_id).strip()

    def current_session_path(self) -> Optional[Path]:
        return self._latest_session()

    def _find_session_by_id(self) -> Optional[Path]:
        session_id = (self._session_id_hint or "").strip()
        if not session_id or not self.root.exists():
            return None
        latest: Optional[Path] = None
        latest_mtime = -1.0
        try:
            for path in self.root.glob(f"**/{session_id}.jsonl"):
                if not path.is_file():
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= latest_mtime:
                    latest_mtime = mtime
                    latest = path
        except Exception:
            return None
        return latest

    def _scan_latest_session(self) -> Optional[Path]:
        if not self.root.exists():
            return None
        heap: List[Tuple[float, str]] = []
        try:
            for path in self.root.glob("**/*.jsonl"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                item = (mtime, str(path))
                if len(heap) < self._scan_limit:
                    heapq.heappush(heap, item)
                else:
                    if item[0] > heap[0][0]:
                        heapq.heapreplace(heap, item)
        except Exception:
            return None

        candidates = sorted(heap, key=lambda x: x[0], reverse=True)
        work_dir_str = str(self.work_dir)
        for _, path_str in candidates:
            path = Path(path_str)
            cwd, _sid = read_droid_session_start(path)
            if not cwd:
                continue
            if _path_is_same_or_parent(work_dir_str, cwd) or _path_is_same_or_parent(cwd, work_dir_str):
                return path
        return None

    def _scan_latest_session_any_project(self) -> Optional[Path]:
        if not self.root.exists():
            return None
        latest: Optional[Path] = None
        latest_mtime = -1.0
        try:
            for path in self.root.glob("**/*.jsonl"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= latest_mtime:
                    latest_mtime = mtime
                    latest = path
        except Exception:
            return None
        return latest

    def _latest_session(self) -> Optional[Path]:
        preferred = self._preferred_session
        scanned = self._scan_latest_session()

        if preferred and preferred.exists():
            if scanned and scanned.exists():
                try:
                    pref_mtime = preferred.stat().st_mtime
                    scan_mtime = scanned.stat().st_mtime
                    if scan_mtime > pref_mtime:
                        self._preferred_session = scanned
                        return scanned
                except OSError:
                    pass
            return preferred

        by_id = self._find_session_by_id()
        if by_id:
            self._preferred_session = by_id
            return by_id

        if scanned:
            self._preferred_session = scanned
            return scanned

        if os.environ.get("DROID_ALLOW_ANY_PROJECT_SCAN") in ("1", "true", "yes"):
            any_latest = self._scan_latest_session_any_project()
            if any_latest:
                self._preferred_session = any_latest
                return any_latest
        return None

    def capture_state(self) -> Dict[str, Any]:
        session = self._latest_session()
        offset = 0
        if session and session.exists():
            try:
                offset = session.stat().st_size
            except OSError:
                offset = 0
        return {"session_path": session, "offset": offset, "carry": b""}

    def wait_for_message(self, state: Dict[str, Any], timeout: float) -> Tuple[Optional[str], Dict[str, Any]]:
        return self._read_since(state, timeout=timeout, block=True)

    def try_get_message(self, state: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        return self._read_since(state, timeout=0.0, block=False)

    def wait_for_events(self, state: Dict[str, Any], timeout: float) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
        return self._read_since_events(state, timeout=timeout, block=True)

    def try_get_events(self, state: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
        return self._read_since_events(state, timeout=0.0, block=False)

    def latest_message(self) -> Optional[str]:
        session = self._latest_session()
        if not session or not session.exists():
            return None
        last: Optional[str] = None
        try:
            with session.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    msg = _extract_message(entry, "assistant")
                    if msg:
                        last = msg
        except OSError:
            return None
        return last

    def latest_conversations(self, n: int = 1) -> List[Tuple[str, str]]:
        session = self._latest_session()
        if not session or not session.exists():
            return []
        pairs: List[Tuple[str, str]] = []
        last_user: Optional[str] = None
        try:
            with session.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    user_msg = _extract_message(entry, "user")
                    if user_msg:
                        last_user = user_msg
                        continue
                    assistant_msg = _extract_message(entry, "assistant")
                    if assistant_msg:
                        pairs.append((last_user or "", assistant_msg))
                        last_user = None
        except OSError:
            return []
        return pairs[-max(1, int(n)) :]

    def _read_since(self, state: Dict[str, Any], timeout: float, block: bool) -> Tuple[Optional[str], Dict[str, Any]]:
        deadline = time.time() + max(0.0, float(timeout)) if block else time.time()
        current_state = dict(state or {})

        while True:
            session = self._latest_session()
            if session is None or not session.exists():
                if not block or time.time() >= deadline:
                    return None, current_state
                time.sleep(self._poll_interval)
                continue

            if current_state.get("session_path") != session:
                current_state["session_path"] = session
                current_state["offset"] = 0
                current_state["carry"] = b""

            message, current_state = self._read_new_messages(session, current_state)
            if message:
                return message, current_state

            if not block or time.time() >= deadline:
                return None, current_state
            time.sleep(self._poll_interval)

    def _read_new_messages(self, session: Path, state: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        offset = int(state.get("offset") or 0)
        carry = state.get("carry") or b""
        try:
            size = session.stat().st_size
        except OSError:
            return None, state

        if size < offset:
            offset = 0
            carry = b""

        try:
            with session.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return None, state

        new_offset = offset + len(data)
        buf = carry + data
        lines = buf.split(b"\n")
        if buf and not buf.endswith(b"\n"):
            carry = lines.pop()
        else:
            carry = b""

        latest: Optional[str] = None
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            msg = _extract_message(entry, "assistant")
            if msg:
                latest = msg

        new_state = {"session_path": session, "offset": new_offset, "carry": carry}
        return latest, new_state

    def _read_since_events(self, state: Dict[str, Any], timeout: float, block: bool) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
        deadline = time.time() + max(0.0, float(timeout)) if block else time.time()
        current_state = dict(state or {})

        while True:
            session = self._latest_session()
            if session is None or not session.exists():
                if not block or time.time() >= deadline:
                    return [], current_state
                time.sleep(self._poll_interval)
                continue

            if current_state.get("session_path") != session:
                current_state["session_path"] = session
                current_state["offset"] = 0
                current_state["carry"] = b""

            events, current_state = self._read_new_events(session, current_state)
            if events:
                return events, current_state

            if not block or time.time() >= deadline:
                return [], current_state
            time.sleep(self._poll_interval)

    def _read_new_events(self, session: Path, state: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
        offset = int(state.get("offset") or 0)
        carry = state.get("carry") or b""
        try:
            size = session.stat().st_size
        except OSError:
            return [], state

        if size < offset:
            offset = 0
            carry = b""

        try:
            with session.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return [], state

        new_offset = offset + len(data)
        buf = carry + data
        lines = buf.split(b"\n")
        if buf and not buf.endswith(b"\n"):
            carry = lines.pop()
        else:
            carry = b""

        events: List[Tuple[str, str]] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            user_msg = _extract_message(entry, "user")
            if user_msg:
                events.append(("user", user_msg))
                continue
            assistant_msg = _extract_message(entry, "assistant")
            if assistant_msg:
                events.append(("assistant", assistant_msg))

        new_state = {"session_path": session, "offset": new_offset, "carry": carry}
        return events, new_state


class DroidCommunicator:
    """Communicate with Droid via terminal and read replies from session logs."""

    def __init__(self, lazy_init: bool = False):
        self.session_info = self._load_session_info()
        if not self.session_info:
            raise RuntimeError("❌ No active Droid session found. Run 'ccb droid' (or add droid to ccb.config) first")

        self.session_id = str(self.session_info.get("session_id") or "").strip()
        self.terminal = self.session_info.get("terminal", "tmux")
        self.pane_id = get_pane_id_from_session(self.session_info) or ""
        self.pane_title_marker = self.session_info.get("pane_title_marker") or ""
        self.backend = get_backend_for_session(self.session_info)
        self.timeout = int(os.environ.get("DROID_SYNC_TIMEOUT", os.environ.get("CCB_SYNC_TIMEOUT", "3600")))
        self.marker_prefix = "dask"
        self.project_session_file = self.session_info.get("_session_file")

        self._log_reader: Optional[DroidLogReader] = None
        self._log_reader_primed = False

        if self.terminal == "wezterm" and self.backend and self.pane_title_marker:
            resolver = getattr(self.backend, "find_pane_by_title_marker", None)
            if callable(resolver):
                resolved = resolver(self.pane_title_marker)
                if resolved:
                    self.pane_id = resolved

        self._publish_registry()

        if not lazy_init:
            self._ensure_log_reader()
            healthy, msg = self._check_session_health()
            if not healthy:
                raise RuntimeError(f"❌ Session unhealthy: {msg}\nHint: run ccb droid (or add droid to ccb.config) to start a new session")

    @property
    def log_reader(self) -> DroidLogReader:
        if self._log_reader is None:
            self._ensure_log_reader()
        return self._log_reader

    def _ensure_log_reader(self) -> None:
        if self._log_reader is not None:
            return
        work_dir_hint = self.session_info.get("work_dir")
        log_work_dir = Path(work_dir_hint) if isinstance(work_dir_hint, str) and work_dir_hint else None
        self._log_reader = DroidLogReader(work_dir=log_work_dir)
        preferred_session = self.session_info.get("droid_session_path")
        if preferred_session:
            self._log_reader.set_preferred_session(Path(str(preferred_session)))
        session_id = self.session_info.get("droid_session_id")
        if session_id:
            self._log_reader.set_session_id_hint(session_id)
        if not self._log_reader_primed:
            self._prime_log_binding()
            self._log_reader_primed = True

    def _find_session_file(self) -> Optional[Path]:
        env_session = (os.environ.get("CCB_SESSION_FILE") or "").strip()
        if env_session:
            try:
                session_path = Path(os.path.expanduser(env_session))
                if session_path.name == ".droid-session" and session_path.is_file():
                    return session_path
            except Exception:
                pass
        return find_project_session_file(Path.cwd(), ".droid-session")

    def _load_session_info(self) -> Optional[dict]:
        project_session = self._find_session_file()
        if not project_session:
            return None
        try:
            with project_session.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or data.get("active", False) is False:
                return None
            data["_session_file"] = str(project_session)
            return data
        except Exception:
            return None

    def _prime_log_binding(self) -> None:
        session_path = self.log_reader.current_session_path()
        if not session_path:
            return
        self._remember_droid_session(session_path)

    def _publish_registry(self) -> None:
        try:
            wd = self.session_info.get("work_dir")
            ccb_pid = compute_ccb_project_id(Path(wd)) if isinstance(wd, str) and wd else ""
            upsert_registry(
                {
                    "ccb_session_id": self.session_id,
                    "ccb_project_id": ccb_pid or None,
                    "work_dir": wd,
                    "terminal": self.terminal,
                    "providers": {
                        "droid": {
                            "pane_id": self.pane_id or None,
                            "pane_title_marker": self.session_info.get("pane_title_marker"),
                            "session_file": self.project_session_file,
                            "droid_session_id": self.session_info.get("droid_session_id"),
                            "droid_session_path": self.session_info.get("droid_session_path"),
                        }
                    },
                }
            )
        except Exception:
            pass

    def _check_session_health(self) -> Tuple[bool, str]:
        return self._check_session_health_impl(probe_terminal=True)

    def _check_session_health_impl(self, probe_terminal: bool) -> Tuple[bool, str]:
        try:
            if not self.pane_id:
                return False, "Session pane id not found"
            if probe_terminal and self.backend:
                pane_alive = self.backend.is_alive(self.pane_id)
                if self.terminal == "wezterm" and self.pane_title_marker and not pane_alive:
                    resolver = getattr(self.backend, "find_pane_by_title_marker", None)
                    if callable(resolver):
                        resolved = resolver(self.pane_title_marker)
                        if resolved:
                            self.pane_id = resolved
                            pane_alive = self.backend.is_alive(self.pane_id)
                if not pane_alive:
                    if self.terminal == "wezterm":
                        err = getattr(self.backend, "last_list_error", None)
                        if err:
                            return False, f"WezTerm CLI error: {err}"
                    return False, f"{self.terminal} session {self.pane_id} not found"
            return True, "Session OK"
        except Exception as exc:
            return False, f"Check failed: {exc}"

    def _remember_droid_session(self, session_path: Path) -> None:
        if not self.project_session_file:
            return
        if not session_path or not isinstance(session_path, Path):
            return
        path = Path(self.project_session_file)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        updated = False
        old_path = str(data.get("droid_session_path") or "").strip()
        old_id = str(data.get("droid_session_id") or "").strip()
        session_path_str = str(session_path)
        binding_changed = False
        if data.get("droid_session_path") != session_path_str:
            data["droid_session_path"] = session_path_str
            updated = True
            binding_changed = True

        _cwd, session_id = read_droid_session_start(session_path)
        if session_id and data.get("droid_session_id") != session_id:
            data["droid_session_id"] = session_id
            updated = True
            binding_changed = True

        if not (data.get("ccb_project_id") or "").strip():
            try:
                wd = data.get("work_dir")
                if isinstance(wd, str) and wd.strip():
                    data["ccb_project_id"] = compute_ccb_project_id(Path(wd.strip()))
                    updated = True
            except Exception:
                pass

        if not updated:
            return

        new_id = str(session_id or "").strip()
        if not new_id:
            try:
                new_id = Path(session_path_str).stem
            except Exception:
                new_id = ""
        if old_id and old_id != new_id:
            data["old_droid_session_id"] = old_id
        if old_path and (old_path != session_path_str or (old_id and old_id != new_id)):
            data["old_droid_session_path"] = old_path
        if (old_path or old_id) and binding_changed:
            data["old_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                from ctx_transfer_utils import maybe_auto_transfer

                old_path_obj = None
                if old_path:
                    try:
                        old_path_obj = Path(old_path).expanduser()
                    except Exception:
                        old_path_obj = None
                wd = data.get("work_dir")
                work_dir = Path(wd) if isinstance(wd, str) and wd else Path.cwd()
                maybe_auto_transfer(
                    provider="droid",
                    work_dir=work_dir,
                    session_path=old_path_obj,
                    session_id=old_id or None,
                )
            except Exception:
                pass

        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        safe_write_session(path, payload)

        try:
            wd = data.get("work_dir")
            ccb_pid = str(data.get("ccb_project_id") or "").strip()
            upsert_registry(
                {
                    "ccb_session_id": self.session_id,
                    "ccb_project_id": ccb_pid or None,
                    "work_dir": wd,
                    "terminal": self.terminal,
                    "providers": {
                        "droid": {
                            "pane_id": self.pane_id or None,
                            "pane_title_marker": data.get("pane_title_marker"),
                            "session_file": str(path),
                            "droid_session_id": data.get("droid_session_id"),
                            "droid_session_path": data.get("droid_session_path"),
                        }
                    },
                }
            )
        except Exception:
            pass

    def ping(self, display: bool = True) -> Tuple[bool, str]:
        healthy, status = self._check_session_health()
        msg = f"✅ Droid connection OK ({status})" if healthy else f"❌ Droid connection error: {status}"
        if display:
            print(msg)
        return healthy, msg

    def get_status(self) -> Dict[str, Any]:
        healthy, status = self._check_session_health()
        return {
            "session_id": self.session_id,
            "terminal": self.terminal,
            "pane_id": self.pane_id,
            "healthy": healthy,
            "status": status,
        }


_ensure_droid_watchdog_started()
