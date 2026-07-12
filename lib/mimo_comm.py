"""
MiMo communication module

Inbound delivery for MiMo:
1) Prefer live pane injection (standard CCB path)
2) Prefer reading assistant output from session JSONL when available
3) Fall back to WezTerm/tmux pane text capture for CCB_DONE detection
4) Inbox files under ~/.mimocode/inbox remain a durable queue when offline
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from ccb_config import apply_backend_env
from ccb_protocol import REQ_ID_PREFIX, is_done_text, extract_reply_for_req
from project_id import compute_ccb_project_id
from session_utils import find_project_session_file, safe_write_session
from terminal import WeztermBackend, get_backend_for_session

apply_backend_env()

_REQ_ID_RE = re.compile(
    rf"{re.escape(REQ_ID_PREFIX)}\s*([0-9a-fA-F]{{32}}|\d{{8}}-\d{{6}}-\d{{3}}-\d+-\d+)"
)


def mimo_home() -> Path:
    override = (os.environ.get("MIMO_HOME") or os.environ.get("MIMOCODE_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mimocode"


def inbox_dir() -> Path:
    return mimo_home() / "inbox"


def replies_dir() -> Path:
    return mimo_home() / "replies"


def compute_mimo_project_id(work_dir: Path) -> str:
    try:
        return compute_ccb_project_id(Path(work_dir))
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
        normalized = str(value or "")
    normalized = normalized.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        normalized = normalized.lower()
    return normalized


class MiMoLogReader:
    """Read MiMo session logs and extract conversations (when JSONL exists)."""

    def __init__(self, work_dir: Path, session_id_filter: str | None = None):
        self.work_dir = work_dir
        self.session_id_filter = session_id_filter

    def _session_dirs(self) -> list[Path]:
        home = Path.home()
        return [
            mimo_home() / "sessions",
            home / ".config" / "mimocode" / "sessions",
            home / ".mimo" / "sessions",
        ]

    def _latest_session(self) -> Path | None:
        candidates: list[Path] = []
        for base in self._session_dirs():
            if not base.exists():
                continue
            try:
                candidates.extend(base.glob("*.jsonl"))
            except Exception:
                continue
        if not candidates:
            return None
        if self.session_id_filter:
            filtered = [p for p in candidates if self.session_id_filter in p.name]
            if filtered:
                candidates = filtered
        candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return candidates[0]

    def latest_conversations(self, n: int = 5) -> list[tuple[str, str]]:
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
                    role = (entry.get("role") or entry.get("type") or "").strip().lower()
                    content = entry.get("content") or entry.get("text") or ""
                    if isinstance(content, list):
                        parts = []
                        for part in content:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                parts.append(part["text"])
                            elif isinstance(part, str):
                                parts.append(part)
                        content = "\n".join(parts)
                    if not isinstance(content, str):
                        continue
                    if role == "user" and content.strip():
                        current_user = content
                    elif role in ("assistant", "model") and current_user:
                        pairs.append((current_user, content))
                        current_user = None
                        if len(pairs) >= n:
                            break
        except Exception:
            pass
        return pairs[-n:] if n > 0 else pairs

    def capture_state(self) -> dict:
        log = self._latest_session()
        offset = -1
        if log and log.exists():
            try:
                offset = log.stat().st_size
            except OSError:
                offset = 0
        return {"log_path": log, "offset": offset}

    def _extract_event(self, entry: dict) -> tuple[str, str] | None:
        role = (entry.get("role") or entry.get("type") or "").strip().lower()
        content = entry.get("content") or entry.get("text") or ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)
        if role in ("user", "assistant", "model") and isinstance(content, str) and content.strip():
            if role == "model":
                role = "assistant"
            return role, content.strip()
        return None

    def wait_for_event(self, state: dict, timeout: float) -> tuple[tuple[str, str] | None, dict]:
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


def _title_looks_like_mimo(title: str) -> bool:
    """Strict title match — avoid false positives (function names, 'communication', etc.)."""
    t = (title or "").strip()
    if not t:
        return False
    # Canonical CCB marker prefixes (set by ccb pane title cmd)
    for prefix in ("CCB-Mimo", "CCB-MiMo", "CCB-mimo", "CCB-MIMO"):
        if t.startswith(prefix):
            return True
    low = t.lower().strip()
    # Exact agent labels / MiMo TUI default titles
    if low in {"mimo", "mimocode", "mimo code", "mimo-code"}:
        return True
    if low.startswith("mimocode"):
        return True
    # MiMo Code often sets titles like "MC | <task summary>"
    if low.startswith("mc |") or low.startswith("mc|"):
        return True
    # Trailing launcher labels: "... - mimo" / "... | mimo" (not snake_case mimo_xxx)
    if re.search(r"(?:^|[\s\|])-\s*mimo(?:code)?\s*$", low):
        return True
    if re.search(r"\|\s*mimo(?:code)?\s*$", low):
        return True
    return False


def count_done_lines(text: str, req_id: str) -> int:
    """Count standalone CCB_DONE lines for req_id (not instructions mentioning the token)."""
    from ccb_protocol import done_line_re

    pat = done_line_re(req_id)
    n = 0
    for ln in (text or "").splitlines():
        if pat.match(ln.rstrip()):
            n += 1
    return n


def pane_reply_is_complete(text: str, req_id: str, *, baseline_done: int = 0) -> bool:
    """
    True when pane text shows a real completion for req_id.

    Ignores DONE tokens that only exist because the prompt was painted on screen
    (baseline_done). Accepts either:
    - a new standalone CCB_DONE line for req_id (even if TUI chrome follows), or
    - classic last-line is_done_text after assistant content.
    """
    if not text or not req_id:
        return False
    done_n = count_done_lines(text, req_id)
    if done_n <= max(0, int(baseline_done)):
        return False

    # New DONE line appeared after baseline — treat as complete even if scrollback
    # ends with MiMo status chrome (footer / mode line).
    if done_n > max(0, int(baseline_done)):
        body = extract_reply_for_req(text, req_id)
        # Reject pure prompt paint: body empty or only scaffolding
        if body:
            stripped = re.sub(
                r"critical instructions:.*?(?=\n\S|\Z)",
                "",
                body,
                flags=re.I | re.S,
            ).strip()
            # Drop box-drawing / footer noise
            cleaned_lines = [
                ln
                for ln in stripped.splitlines()
                if ln.strip()
                and not ln.strip().startswith("┃")
                and "esc interrupt" not in ln.lower()
                and "tab switch" not in ln.lower()
            ]
            if cleaned_lines:
                return True
        # Fallback: last-line DONE style
        if is_done_text(text, req_id):
            return True
        # If DONE count increased and req_id is present, accept
        return True

    return False


def find_mimo_pane(session_data: dict | None = None) -> tuple[Optional[object], Optional[str]]:
    """
    Locate a live MiMo pane.
    Returns (backend, pane_id) or (None, None).
    """
    data = dict(session_data or {})
    if not data.get("terminal"):
        # Prefer WezTerm on Windows native CCB setups.
        if os.environ.get("WEZTERM_PANE") or os.name == "nt":
            data["terminal"] = "wezterm"
        else:
            data["terminal"] = "tmux"

    backend = get_backend_for_session(data)
    if not backend:
        # Last-ditch WezTerm backend
        try:
            backend = WeztermBackend()
        except Exception:
            backend = None
    if not backend:
        return None, None

    # 1) Strict marker match first (most reliable)
    markers = [
        str(data.get("pane_title_marker") or "").strip(),
        "CCB-Mimo",
        "CCB-MiMo",
        "CCB-mimo",
    ]
    resolver = getattr(backend, "find_pane_by_title_marker", None)
    if callable(resolver):
        for marker in markers:
            if not marker:
                continue
            try:
                resolved = resolver(marker)
            except Exception:
                resolved = None
            if resolved:
                try:
                    if backend.is_alive(str(resolved)):
                        return backend, str(resolved)
                except Exception:
                    pass

    # 2) Stored pane_id only if title still looks like MiMo (avoid stale/wrong binding)
    pane_id = str(data.get("pane_id") or "").strip()
    if pane_id:
        try:
            if backend.is_alive(pane_id):
                title = ""
                list_fn = getattr(backend, "_list_panes", None)
                if callable(list_fn):
                    for pane in (list_fn() or []):
                        if str(pane.get("pane_id")) == str(pane_id):
                            title = str(pane.get("title") or "")
                            break
                if _title_looks_like_mimo(title) or not title:
                    # If we cannot read title, trust stored binding only when marker was set
                    if _title_looks_like_mimo(title) or data.get("pane_title_marker"):
                        if _title_looks_like_mimo(title):
                            return backend, pane_id
        except Exception:
            pass

    # 3) Full scan with strict title rules
    list_fn = getattr(backend, "_list_panes", None)
    if callable(list_fn):
        try:
            panes = list_fn() or []
        except Exception:
            panes = []
        for pane in panes:
            title = str(pane.get("title") or "")
            if not _title_looks_like_mimo(title):
                continue
            pid = pane.get("pane_id")
            if pid is None:
                continue
            try:
                if backend.is_alive(str(pid)):
                    return backend, str(pid)
            except Exception:
                continue

    return backend, None


def poll_pane_for_done(
    backend: object,
    pane_id: str,
    req_id: str,
    *,
    timeout_s: float,
    cancel_event=None,
    idle_timeout_s: float = 20.0,
) -> tuple[str, bool]:
    """
    Poll pane text for CCB_DONE marker.
    Returns (reply_text, done_seen).
    """
    deadline = None if float(timeout_s) < 0 else (time.time() + float(timeout_s))
    get_text = getattr(backend, "get_text", None) or getattr(backend, "get_pane_content", None)
    if not callable(get_text):
        return "", False

    last_snapshot = ""
    last_change = time.time()
    best_reply = ""

    while True:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            break
        if deadline is not None and time.time() >= deadline:
            break

        try:
            text = get_text(pane_id, 200) or ""
        except TypeError:
            try:
                text = get_text(pane_id) or ""
            except Exception:
                text = ""
        except Exception:
            text = ""

        if text:
            if text != last_snapshot:
                last_snapshot = text
                last_change = time.time()
            # Prefer extract_reply if done present; else keep growing snapshot tail.
            if is_done_text(text, req_id) or (req_id in text and "CCB_DONE" in text):
                reply = extract_reply_for_req(text, req_id)
                if not reply.strip():
                    # crude extract: after last req id mention until done
                    reply = text
                return reply.strip(), True
            best_reply = text
            # Idle completion without exact DONE (MiMo often omits)
            if idle_timeout_s > 0 and (time.time() - last_change) >= idle_timeout_s and text.strip():
                # Only accept idle if our req_id appears (message reached pane)
                if req_id in text or REQ_ID_PREFIX in text:
                    return text.strip(), True

        time.sleep(0.5)

    return best_reply.strip(), False


class MiMoSessionManager:
    """Manage MiMo session file binding and pane resolution."""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir

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

    def rebind_live_pane(self) -> tuple[bool, str]:
        data = self.ensure_session()
        backend, pane_id = find_mimo_pane(data)
        if backend and pane_id:
            data["pane_id"] = pane_id
            data["active"] = True
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if not data.get("terminal"):
                data["terminal"] = "wezterm"
            self._write_session(data)
            return True, pane_id
        return False, str(data.get("pane_id") or "")

    def get_pane_id(self) -> str | None:
        data = self._read_session()
        return data.get("pane_id")

    def update_pane_id(self, pane_id: str) -> None:
        data = self._read_session()
        data["pane_id"] = pane_id
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_session(data)

    def send_to_pane(self, text: str) -> bool:
        data = self._read_session()
        backend, pane_id = find_mimo_pane(data)
        if not backend or not pane_id:
            return False
        try:
            backend.send_text(pane_id, text)
            if str(data.get("pane_id") or "") != str(pane_id):
                self.update_pane_id(str(pane_id))
            return True
        except Exception:
            return False


class MiMoCommunicator:
    """Connectivity helper for ccb-ping / diagnostics."""

    def __init__(self, work_dir: Path | None = None):
        self.work_dir = work_dir or Path.cwd()

    def ping(self, display: bool = True) -> Tuple[bool, str]:
        session_file = find_project_session_file(self.work_dir, ".mimo-session")
        data: dict = {}
        if session_file and session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}

        backend, pane_id = find_mimo_pane(data)
        pane_ok = bool(backend and pane_id)
        inbox_ok = inbox_dir().exists()

        reader = MiMoLogReader(self.work_dir)
        log = reader._latest_session()
        log_ok = bool(log and log.exists())

        if pane_ok:
            msg = f"MiMo OK pane={pane_id}" + (f" log={log.name}" if log_ok else " (no session jsonl; pane-poll mode)")
            if display:
                print(msg)
            return True, msg

        if inbox_ok:
            pending = list(inbox_dir().glob("*.json"))
            msg = f"MiMo degraded: no live pane; inbox ready ({len(pending)} files). Run `ccb` to mount MiMo."
            if display:
                print(msg)
            return False, msg

        msg = "MiMo offline: no pane and no ~/.mimocode/inbox"
        if display:
            print(msg)
        return False, msg
