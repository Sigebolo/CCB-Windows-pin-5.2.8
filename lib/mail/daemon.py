"""
Mail daemon (maild) for CCB.

Version 3: ASK-based mail system
- Routes emails to ASK system via AskHandler
- Provider extracted from body prefix: "CLAUDE: message"
- Replies via ccb-completion-hook with CCB_CALLER=email

Version 2 (deprecated): Pane-based notification system
"""

import json
import os
import subprocess
import signal
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from threading import Event, Thread
from typing import Optional, Callable, List, Dict

from .config import MailConfig, load_config, get_config_dir, SUPPORTED_PROVIDERS
from .poller import ImapPoller, ImapPollerDaemon
from .sender import SmtpSender
from .router import RoutedMessage
from .threads import get_thread_store, ThreadStore
from .attachments import cleanup_old_attachments
from .ask_handler import AskHandler

# State file for daemon discovery
STATE_FILE = "maild.json"
PID_FILE = "maild.pid"
LOG_FILE = "maild.log"


@dataclass
class DaemonState:
    """Daemon state for discovery."""
    pid: int
    started_at: float
    email: str
    status: str = "running"
    version: int = 3
    enabled_hooks: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DaemonState":
        return cls(
            pid=data.get("pid", 0),
            started_at=data.get("started_at", 0),
            email=data.get("email", ""),
            status=data.get("status", "unknown"),
            version=data.get("version", 1),
            enabled_hooks=data.get("enabled_hooks", []),
        )


def get_state_path() -> Path:
    """Get path to daemon state file."""
    return get_config_dir() / STATE_FILE


def get_pid_path() -> Path:
    """Get path to PID file."""
    return get_config_dir() / PID_FILE


def get_log_path() -> Path:
    """Get path to log file."""
    return get_config_dir() / LOG_FILE


def read_daemon_state() -> Optional[DaemonState]:
    """Read daemon state from file."""
    state_path = get_state_path()
    if not state_path.exists():
        return None
    try:
        with open(state_path, "r") as f:
            data = json.load(f)
        return DaemonState.from_dict(data)
    except Exception:
        return None


def write_daemon_state(state: DaemonState) -> None:
    """Write daemon state to file."""
    state_path = get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    state_path.chmod(0o600)


def remove_daemon_state() -> None:
    """Remove daemon state file."""
    state_path = get_state_path()
    if state_path.exists():
        state_path.unlink()
    pid_path = get_pid_path()
    if pid_path.exists():
        pid_path.unlink()


def _read_pid_file() -> Optional[int]:
    """Read PID from pid file."""
    pid_path = get_pid_path()
    if not pid_path.exists():
        return None
    try:
        pid = int(pid_path.read_text().strip())
        return pid if pid > 0 else None
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    """Check if process is alive, cross-platform."""
    if pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False

            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, SystemError):
        return False


def _get_running_pid(state: Optional[DaemonState]) -> Optional[int]:
    """Resolve active daemon PID from state and pid file."""
    candidates: List[int] = []
    if state and state.pid:
        candidates.append(state.pid)

    pid_file = _read_pid_file()
    if pid_file and pid_file not in candidates:
        candidates.append(pid_file)

    for pid in candidates:
        if _is_process_alive(pid):
            return pid
    return None


def is_daemon_running() -> bool:
    """Check if daemon is running."""
    state = read_daemon_state()
    running_pid = _get_running_pid(state)
    if running_pid:
        if state and state.pid != running_pid:
            state.pid = running_pid
            write_daemon_state(state)
        return True

    # Process not running, clean up stale state
    remove_daemon_state()
    return False


class MailDaemon:
    """Mail daemon service (v3 with ASK routing)."""

    def __init__(
        self,
        config: Optional[MailConfig] = None,
        message_handler: Optional[Callable[[RoutedMessage], None]] = None,
    ):
        self.config = config or load_config()
        self.message_handler = message_handler or self._default_message_handler
        self.thread_store = get_thread_store()
        self.poller: Optional[ImapPollerDaemon] = None
        self.sender: Optional[SmtpSender] = None
        self.ask_handler: Optional[AskHandler] = None
        self._stop_event = Event()
        self._cleanup_thread: Optional[Thread] = None

    def _get_service_email(self) -> str:
        """Get service account email."""
        if hasattr(self.config, 'service_account'):
            return self.config.service_account.email
        return self.config.account.email

    def _default_message_handler(self, msg: RoutedMessage) -> bool:
        """Default message handler - v3 uses ASK system.

        Returns:
            True if message was successfully processed, False otherwise.
        """
        # V3 mode: route to ASK system
        if self.ask_handler:
            result = self.ask_handler.handle_email(msg)
            if result.success:
                print(f"[maild] {result.message} (req={result.request_id})")
                return True
            else:
                print(f"[maild] ASK handler failed: {result.message}")
                return False

        # Fallback: validate sender and log
        from .router import MessageRouter
        router = MessageRouter(self.config)

        if not router.is_sender_allowed(msg.from_addr):
            print(f"[maild] Rejected message from unauthorized sender: {msg.from_addr}")
            return False

        print(f"[maild] No handler available for message: {msg.subject}")
        return False

    def start(self) -> None:
        """Start the mail daemon."""
        if not self.config.enabled:
            print("[maild] Mail service is disabled")
            return

        if is_daemon_running():
            print("[maild] Daemon already running")
            return

        # Write state
        state = DaemonState(
            pid=os.getpid(),
            started_at=time.time(),
            email=self._get_service_email(),
            status="running",
            version=3,
        )
        write_daemon_state(state)

        # Write PID file
        pid_path = get_pid_path()
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Initialize v3 ASK handler
        self.ask_handler = AskHandler(self.config)

        # Start IMAP poller
        self.poller = ImapPollerDaemon(self.config, self.message_handler)
        self.poller.start()

        # Start cleanup thread
        self._cleanup_thread = Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        print(f"[maild] Started for {self._get_service_email()} (v3 ASK mode)")

        # Main loop
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1)
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the mail daemon."""
        print("[maild] Stopping...")
        self._stop_event.set()

        if self.poller:
            self.poller.stop()

        if self.sender:
            self.sender.disconnect()

        remove_daemon_state()
        print("[maild] Stopped")

    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals."""
        print(f"[maild] Received signal {signum}")
        self._stop_event.set()

    def _cleanup_loop(self) -> None:
        """Periodic cleanup of old data."""
        while not self._stop_event.is_set():
            try:
                # Cleanup old threads
                removed = self.thread_store.cleanup_old()
                if removed:
                    print(f"[maild] Cleaned up {removed} old threads")

                # Cleanup old attachments
                removed = cleanup_old_attachments()
                if removed:
                    print(f"[maild] Cleaned up {removed} old attachment dirs")
            except Exception as e:
                print(f"[maild] Cleanup error: {e}")

            # Run cleanup every hour
            self._stop_event.wait(3600)


def start_daemon(foreground: bool = False) -> None:
    """Start the mail daemon."""
    config = load_config()
    if not config.enabled:
        print("Mail service is not enabled. Run 'ccb mail setup' first.")
        sys.exit(1)

    daemon = MailDaemon(config)

    if foreground:
        daemon.start()
    else:
        # Windows fallback: spawn detached foreground process.
        # os.fork() is not available on Windows.
        if os.name == "nt":
            project_root = Path(__file__).resolve().parents[2]
            launcher = project_root / "bin" / "maild"
            log_path = get_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)

            creationflags = 0
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)

            with open(log_path, "a", buffering=1) as log_file:
                proc = subprocess.Popen(
                    [sys.executable, str(launcher), "start", "--foreground"],
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=log_file,
                    cwd=str(project_root),
                    close_fds=True,
                    creationflags=creationflags,
                )

            print(f"[maild] Started in background (PID: {proc.pid})")
            return

        # Daemonize (POSIX)
        pid = os.fork()
        if pid > 0:
            print(f"[maild] Started in background (PID: {pid})")
            sys.exit(0)

        # Child process
        os.setsid()
        os.umask(0)

        # Redirect stdout/stderr to log file using dup2 for proper redirection
        log_path = get_log_path()
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(log_fd, 1)  # stdout
        os.dup2(log_fd, 2)  # stderr
        os.close(log_fd)
        sys.stdout = os.fdopen(1, "w", buffering=1)  # Line buffered
        sys.stderr = sys.stdout

        daemon.start()


def stop_daemon() -> bool:
    """Stop the mail daemon."""
    state = read_daemon_state()
    pid = None
    if state:
        pid = state.pid
    if not pid:
        pid = _read_pid_file()

    if not pid:
        print("Mail daemon is not running")
        return False

    if not _is_process_alive(pid):
        print("Mail daemon is not running")
        remove_daemon_state()
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to maild (PID: {pid})")

        # Wait for process to exit
        for _ in range(20):
            if not _is_process_alive(pid):
                print("Mail daemon stopped")
                remove_daemon_state()
                return True
            time.sleep(0.25)

        print("Warning: Daemon did not stop gracefully")
        return False
    except (OSError, ProcessLookupError, SystemError):
        print("Mail daemon is not running")
        remove_daemon_state()
        return False


def get_daemon_status() -> dict:
    """Get daemon status."""
    state = read_daemon_state()
    if not state:
        return {"running": False}

    running_pid = _get_running_pid(state)
    if running_pid:
        if state.pid != running_pid:
            state.pid = running_pid
            write_daemon_state(state)
        return {
            "running": True,
            "pid": running_pid,
            "email": state.email,
            "started_at": state.started_at,
            "uptime": time.time() - state.started_at,
            "version": getattr(state, 'version', 1),
            "enabled_hooks": getattr(state, 'enabled_hooks', []),
        }
    else:
        remove_daemon_state()
        return {"running": False}


def set_pane_id(provider: str, pane_id: str) -> bool:
    """Set pane ID for a provider (for daemon to use)."""
    state_path = get_config_dir() / "pane_ids.json"
    try:
        pane_ids = {}
        if state_path.exists():
            with open(state_path, "r") as f:
                pane_ids = json.load(f)
        pane_ids[provider] = pane_id
        with open(state_path, "w") as f:
            json.dump(pane_ids, f, indent=2)
        return True
    except Exception:
        return False


def get_pane_ids() -> Dict[str, str]:
    """Get all registered pane IDs."""
    state_path = get_config_dir() / "pane_ids.json"
    try:
        if state_path.exists():
            with open(state_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}
