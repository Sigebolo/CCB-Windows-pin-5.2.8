"""
MiMo provider adapter for the unified ask daemon.

Inbound path (others -> MiMo):
1. Resolve/rebind live MiMo pane (title marker CCB-Mimo)
2. Inject CCB-wrapped prompt into pane
3. Wait for CCB_DONE via session JSONL (if any) or pane text capture
4. Durable inbox queue under ~/.mimocode/inbox as offline fallback
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from askd.adapters.base import BaseProviderAdapter, ProviderRequest, ProviderResult, QueuedTask
from askd_runtime import log_path, write_log
from ccb_protocol import (
    REQ_ID_PREFIX,
    is_done_text,
    extract_reply_for_req,
    wrap_mimo_prompt,
)
from masksd_session import MimoProjectSession, compute_session_key, load_project_session
from mimo_comm import (
    MiMoLogReader,
    MiMoSessionManager,
    count_done_lines,
    find_mimo_pane,
    inbox_dir,
    pane_reply_is_complete,
    replies_dir,
)
from completion_hook import (
    COMPLETION_STATUS_CANCELLED,
    COMPLETION_STATUS_COMPLETED,
    COMPLETION_STATUS_FAILED,
    COMPLETION_STATUS_INCOMPLETE,
    default_reply_for_status,
    notify_completion,
)
from providers import MASKD_SPEC
from session_utils import safe_write_session
from terminal import get_backend_for_session, is_windows


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_log(line: str) -> None:
    write_log(log_path(MASKD_SPEC.log_file_name), line)


class MimoAdapter(BaseProviderAdapter):
    """Adapter for MiMo provider."""

    @property
    def key(self) -> str:
        return "mimo"

    @property
    def spec(self):
        return MASKD_SPEC

    @property
    def session_filename(self) -> str:
        return ".mimo-session"

    def load_session(self, work_dir: Path) -> Optional[MimoProjectSession]:
        return load_project_session(work_dir)

    def compute_session_key(self, session: Any) -> str:
        return compute_session_key(session) if session else "mimo:unknown"

    def _ensure_bound_pane(self, work_dir: Path) -> tuple[Optional[MimoProjectSession], Optional[object], Optional[str]]:
        """Load/create session and rebind to a live MiMo pane if possible."""
        session = load_project_session(work_dir)
        mgr = MiMoSessionManager(work_dir)
        if not session:
            # Create a minimal session file so later asks have a binding target.
            data = mgr.ensure_session()
            session = load_project_session(work_dir)
        else:
            data = dict(session.data)

        # Prefer ensure_pane first (uses stored pane_id + marker).
        if session:
            ok, pane_or_err = session.ensure_pane()
            if ok:
                backend = get_backend_for_session(session.data)
                return session, backend, pane_or_err

        # Global discovery by title marker / "mimo" in title.
        backend, pane_id = find_mimo_pane(data if isinstance(data, dict) else {})
        if backend and pane_id:
            # Persist rebinding
            try:
                if session:
                    session.data["pane_id"] = pane_id
                    session.data["active"] = True
                    session.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if not session.data.get("pane_title_marker"):
                        session.data["pane_title_marker"] = "CCB-Mimo"
                    session._write_back()
                else:
                    mgr.update_pane_id(pane_id)
                    session = load_project_session(work_dir)
            except Exception as exc:
                _write_log(f"[WARN] rebind write failed: {exc}")
            return session, backend, pane_id

        return session, None, None

    def _write_inbox(self, task: QueuedTask, prompt: str) -> Path | None:
        req = task.request
        inbox = inbox_dir()
        replies = replies_dir()
        inbox.mkdir(parents=True, exist_ok=True)
        replies.mkdir(parents=True, exist_ok=True)

        task_id = f"ccb-{task.req_id}"
        reply_path = replies / f"{task_id}.reply.json"
        expires_at = datetime.now() + timedelta(seconds=max(float(req.timeout_s), 60))
        payload = {
            "task_id": task_id,
            "sender": req.caller or "unknown",
            "message": prompt,  # already CCB-wrapped for MiMo to process
            "raw_message": req.message,
            "req_id": task.req_id,
            "reply_path": str(reply_path),
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at.isoformat(),
            "status": "pending",
            "work_dir": req.work_dir,
        }
        target = inbox / f"{task_id}.json"
        tmp = target.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            tmp.replace(target)
            _write_log(f"[INFO] inbox queued task_id={task_id}")
            return reply_path
        except Exception as exc:
            _write_log(f"[ERROR] inbox write failed: {exc}")
            return None

    def _poll_reply_file(self, reply_path: Path, task: QueuedTask, timeout_s: float) -> Optional[str]:
        deadline = time.time() + max(float(timeout_s), 1.0)
        while time.time() < deadline:
            if task.cancel_event and task.cancel_event.is_set():
                return None
            if reply_path.exists():
                try:
                    data = json.loads(reply_path.read_text(encoding="utf-8"))
                    reply = data.get("reply")
                    if isinstance(reply, str) and reply.strip():
                        # Ignore stub echo replies from unfinished scanner
                        if reply.startswith("MiMo received your message from "):
                            _write_log("[WARN] ignoring stub inbox echo reply")
                        else:
                            return reply.strip()
                except Exception:
                    pass
            time.sleep(0.5)
        return None

    def _wait_for_reply(
        self,
        *,
        task: QueuedTask,
        work_dir: Path,
        backend: object,
        pane_id: str,
        prompt: str,
        reply_path: Path | None,
    ) -> tuple[str, bool, bool]:
        """
        Wait for MiMo reply via log events and/or pane text.
        Returns (reply, done_seen, anchor_seen).
        """
        req = task.request
        reader = MiMoLogReader(work_dir=work_dir)
        state = reader.capture_state()
        chunks: list[str] = []
        anchor_seen = False
        done_seen = False

        idle_timeout = float(os.environ.get("CCB_MASKD_IDLE_TIMEOUT", "20.0"))
        deadline = None if float(req.timeout_s) < 0.0 else (time.time() + float(req.timeout_s))
        last_reply_snapshot = ""
        last_reply_changed_at = time.time()
        last_pane_check = time.time()
        pane_check_interval = float(
            os.environ.get("CCB_MASKD_PANE_CHECK_INTERVAL", "5.0" if is_windows() else "2.0")
        )
        anchor_collect_grace = time.time() + 3.0
        get_text = getattr(backend, "get_text", None) or getattr(backend, "get_pane_content", None)

        def _read_pane() -> str:
            if not callable(get_text):
                return ""
            try:
                return get_text(pane_id, 300) or ""
            except TypeError:
                try:
                    return get_text(pane_id) or ""
                except Exception:
                    return ""
            except Exception:
                return ""

        # Inject after capturing log offset so we don't miss the user event.
        try:
            # Escape any stuck mode in TUI before paste
            send_key = getattr(backend, "send_key", None)
            if callable(send_key):
                try:
                    send_key(pane_id, "Escape")
                    time.sleep(0.15)
                except Exception:
                    pass
            backend.send_text(pane_id, prompt)
            # MiMo sometimes needs a second Enter after bracketed paste
            if callable(send_key):
                time.sleep(float(os.environ.get("CCB_MIMO_ENTER_DELAY", "0.35")))
                try:
                    send_key(pane_id, "Enter")
                except Exception:
                    pass
        except Exception as exc:
            _write_log(f"[ERROR] mimo inject failed: {exc}")
            return "", False, False

        _write_log(f"[INFO] injected into mimo pane={pane_id} req_id={task.req_id}")

        # Baseline AFTER paint so we ignore DONE tokens that only exist in the prompt paint.
        time.sleep(float(os.environ.get("CCB_MIMO_BASELINE_DELAY", "0.6")))
        baseline_text = _read_pane()
        baseline_done = count_done_lines(baseline_text, task.req_id)
        last_reply_snapshot = baseline_text
        last_reply_changed_at = time.time()

        while True:
            if task.cancel_event and task.cancel_event.is_set():
                break
            if deadline is not None and time.time() >= deadline:
                break

            wait_step = 0.5
            if deadline is not None:
                wait_step = min(0.5, max(0.05, deadline - time.time()))

            # Pane liveness
            if time.time() - last_pane_check >= pane_check_interval:
                try:
                    alive = bool(backend.is_alive(pane_id))
                except Exception:
                    alive = False
                if not alive:
                    sess2, backend2, pane2 = self._ensure_bound_pane(work_dir)
                    if backend2 and pane2:
                        backend, pane_id = backend2, pane2
                        get_text = getattr(backend, "get_text", None) or getattr(
                            backend, "get_pane_content", None
                        )
                        _write_log(f"[WARN] re-bound mimo pane -> {pane_id}")
                    else:
                        _write_log(f"[ERROR] mimo pane died req_id={task.req_id}")
                        break
                last_pane_check = time.time()

            # 1) Log reader path (preferred when MiMo writes JSONL)
            event, state = reader.wait_for_event(state, wait_step)
            if event is not None:
                role, text = event
                if role == "user":
                    if f"{REQ_ID_PREFIX} {task.req_id}" in text or task.req_id in text:
                        anchor_seen = True
                    continue
                if role == "assistant":
                    if (not anchor_seen) and time.time() < anchor_collect_grace:
                        continue
                    chunks.append(text)
                    combined = "\n".join(chunks)
                    if is_done_text(combined, task.req_id):
                        reply = extract_reply_for_req(combined, task.req_id) or combined
                        return reply, True, True
                    if combined != last_reply_snapshot:
                        last_reply_snapshot = combined
                        last_reply_changed_at = time.time()
                    elif combined and (time.time() - last_reply_changed_at >= idle_timeout):
                        # Idle only if we have non-empty assistant chunks after anchor
                        if anchor_seen and len(combined.strip()) >= 3:
                            return combined, True, True

            # 2) Pane text path (no JSONL)
            pane_text = _read_pane()
            if pane_text:
                if task.req_id in pane_text or REQ_ID_PREFIX in pane_text:
                    anchor_seen = True
                if pane_reply_is_complete(pane_text, task.req_id, baseline_done=baseline_done):
                    reply = extract_reply_for_req(pane_text, task.req_id) or pane_text
                    # Drop obvious UI chrome lines
                    cleaned = "\n".join(
                        ln
                        for ln in reply.splitlines()
                        if "CRITICAL INSTRUCTIONS" not in ln
                        and not ln.strip().startswith("┃")
                        and "esc interrupt" not in ln.lower()
                    ).strip()
                    return (cleaned or reply).strip(), True, True
                if pane_text != last_reply_snapshot:
                    last_reply_snapshot = pane_text
                    last_reply_changed_at = time.time()
                elif (
                    anchor_seen
                    and pane_text != baseline_text
                    and (time.time() - last_reply_changed_at >= idle_timeout)
                ):
                    # Content settled but no DONE — only accept if substantially beyond baseline
                    if len(pane_text) > len(baseline_text) + 20:
                        reply = extract_reply_for_req(pane_text, task.req_id) or pane_text
                        cleaned = "\n".join(
                            ln
                            for ln in reply.splitlines()
                            if "CRITICAL INSTRUCTIONS" not in ln
                            and not ln.strip().startswith("┃")
                        ).strip()
                        if cleaned and "CRITICAL INSTRUCTIONS" not in cleaned:
                            return cleaned, True, True

            # 3) Reply file
            if reply_path is not None and reply_path.exists():
                try:
                    data = json.loads(reply_path.read_text(encoding="utf-8"))
                    reply = data.get("reply")
                    if isinstance(reply, str) and reply.strip() and not reply.startswith(
                        "MiMo received your message from "
                    ):
                        return reply.strip(), True, True
                except Exception:
                    pass

        combined = "\n".join(chunks).strip() or last_reply_snapshot.strip()
        return combined, done_seen, anchor_seen

    def handle_task(self, task: QueuedTask) -> ProviderResult:
        started_ms = _now_ms()
        req = task.request
        work_dir = Path(req.work_dir)
        _write_log(
            f"[INFO] start provider=mimo req_id={task.req_id} work_dir={req.work_dir} caller={req.caller}"
        )

        prompt = req.message if req.no_wrap else wrap_mimo_prompt(req.message, task.req_id)

        # Always queue inbox for durability / offline catch-up.
        reply_path = self._write_inbox(task, prompt)

        session, backend, pane_id = self._ensure_bound_pane(work_dir)
        session_key = self.compute_session_key(session) if session else "mimo:unknown"

        if not backend or not pane_id:
            _write_log(f"[ERROR] no live MiMo pane for req_id={task.req_id}; left in inbox")
            # Wait a bit for a MiMo-side worker to process inbox if one is running.
            if reply_path is not None:
                reply = self._poll_reply_file(reply_path, task, min(float(req.timeout_s), 15.0))
                if reply:
                    result = ProviderResult(
                        exit_code=0,
                        reply=reply,
                        req_id=task.req_id,
                        session_key=session_key,
                        done_seen=True,
                        status=COMPLETION_STATUS_COMPLETED,
                    )
                    notify_completion(
                        provider="mimo",
                        output_file=req.output_path,
                        reply=reply,
                        req_id=task.req_id,
                        done_seen=True,
                        status=COMPLETION_STATUS_COMPLETED,
                        caller=req.caller,
                        email_req_id=req.email_req_id,
                        email_msg_id=req.email_msg_id,
                        email_from=req.email_from,
                        work_dir=req.work_dir,
                    )
                    return result

            msg = (
                "No live MiMo pane. Message queued in ~/.mimocode/inbox. "
                "Start MiMo via `ccb` (or mount mimo) so inbound SMS can inject."
            )
            result = ProviderResult(
                exit_code=1,
                reply=msg,
                req_id=task.req_id,
                session_key=session_key,
                done_seen=False,
                status=COMPLETION_STATUS_FAILED,
            )
            notify_completion(
                provider="mimo",
                output_file=req.output_path,
                reply=msg,
                req_id=task.req_id,
                done_seen=False,
                status=COMPLETION_STATUS_FAILED,
                caller=req.caller,
                email_req_id=req.email_req_id,
                email_msg_id=req.email_msg_id,
                email_from=req.email_from,
                work_dir=req.work_dir,
            )
            return result

        # Mark inbox entry as delivered-to-pane
        try:
            task_id = f"ccb-{task.req_id}"
            inbox_file = inbox_dir() / f"{task_id}.json"
            if inbox_file.exists():
                data = json.loads(inbox_file.read_text(encoding="utf-8"))
                data["status"] = "delivered_to_pane"
                data["pane_id"] = pane_id
                data["delivered_at"] = datetime.now().isoformat()
                inbox_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        reply, done_seen, anchor_seen = self._wait_for_reply(
            task=task,
            work_dir=work_dir,
            backend=backend,
            pane_id=pane_id,
            prompt=prompt,
            reply_path=reply_path,
        )

        if task.cancelled:
            status = COMPLETION_STATUS_CANCELLED
        elif done_seen:
            status = COMPLETION_STATUS_COMPLETED
        else:
            status = COMPLETION_STATUS_INCOMPLETE

        if not reply.strip():
            reply = default_reply_for_status(status, done_seen=done_seen)

        # Mark inbox completed when we got a real reply
        if done_seen and reply_path is not None:
            try:
                task_id = f"ccb-{task.req_id}"
                inbox_file = inbox_dir() / f"{task_id}.json"
                if inbox_file.exists():
                    data = json.loads(inbox_file.read_text(encoding="utf-8"))
                    data["status"] = "completed"
                    data["completed_at"] = datetime.now().isoformat()
                    inbox_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                # Also write reply file for any pollers
                payload = {
                    "task_id": task_id,
                    "reply": reply,
                    "completed_at": datetime.now().isoformat(),
                    "req_id": task.req_id,
                }
                tmp = reply_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(reply_path)
            except Exception as exc:
                _write_log(f"[WARN] failed to finalize inbox/reply: {exc}")

        result = ProviderResult(
            exit_code=0 if done_seen else 2,
            reply=reply,
            req_id=task.req_id,
            session_key=session_key,
            done_seen=done_seen,
            done_ms=_now_ms() - started_ms if done_seen else None,
            anchor_seen=anchor_seen,
            status=status,
        )
        _write_log(
            f"[INFO] done provider=mimo req_id={task.req_id} exit={result.exit_code} "
            f"anchor={anchor_seen} done={done_seen} pane={pane_id}"
        )
        notify_completion(
            provider="mimo",
            output_file=req.output_path,
            reply=reply,
            req_id=task.req_id,
            done_seen=done_seen,
            status=status,
            caller=req.caller,
            email_req_id=req.email_req_id,
            email_msg_id=req.email_msg_id,
            email_from=req.email_from,
            work_dir=req.work_dir,
        )
        return result
