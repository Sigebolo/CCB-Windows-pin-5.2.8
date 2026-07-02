"""
IMAP polling for CCB Mail.

Supports both traditional polling and IMAP IDLE for real-time notifications.
"""

import email
import imaplib
import select
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, List, Optional
from threading import Thread, Event

from .config import MailConfig, ImapConfig
from .credentials import get_password
from .router import MessageRouter, RoutedMessage
from .attachments import extract_attachments


@dataclass
class PollResult:
    """Result of a poll operation."""
    success: bool
    messages: List[RoutedMessage]
    error: Optional[str] = None


class ImapPoller:
    """IMAP email poller."""

    def __init__(self, config: MailConfig):
        self.config = config
        # Support both v1 (account) and v2 (service_account) config
        if hasattr(config, 'service_account'):
            self.imap_config = config.service_account.imap
            self.email_addr = config.service_account.email
        else:
            self.imap_config = config.account.imap
            self.email_addr = config.account.email
        self.router = MessageRouter(config)
        self._connection: Optional[imaplib.IMAP4_SSL] = None

    def _create_connection(self) -> imaplib.IMAP4_SSL:
        """Create IMAP connection."""
        if self.imap_config.ssl:
            context = ssl.create_default_context()
            conn = imaplib.IMAP4_SSL(
                self.imap_config.host,
                self.imap_config.port,
                ssl_context=context,
            )
        else:
            conn = imaplib.IMAP4(self.imap_config.host, self.imap_config.port)

        return conn

    def connect(self) -> bool:
        """Connect and authenticate to IMAP server."""
        try:
            password = get_password(self.email_addr)
            if not password:
                raise ValueError(f"No password stored for {self.email_addr}")

            self._connection = self._create_connection()
            self._connection.login(self.email_addr, password)
            return True
        except Exception as e:
            print(f"IMAP connection failed: {e}")
            self._connection = None
            return False

    def disconnect(self) -> None:
        """Disconnect from IMAP server."""
        if self._connection:
            try:
                self._connection.logout()
            except Exception:
                pass
            self._connection = None

    def test_connection(self) -> tuple[bool, str]:
        """
        Test IMAP connection.

        Returns:
            Tuple of (success, message)
        """
        try:
            if self.connect():
                # Test IDLE capability
                idle_supported = self.supports_idle()
                self.disconnect()
                if idle_supported:
                    return True, "IMAP connection successful (IDLE supported)"
                return True, "IMAP connection successful (IDLE not supported, using polling)"
            return False, "IMAP authentication failed"
        except Exception as e:
            return False, f"IMAP connection error: {e}"

    def supports_idle(self) -> bool:
        """Check if server supports IMAP IDLE."""
        if not self._connection:
            return False
        try:
            # Check CAPABILITY for IDLE support
            status, caps = self._connection.capability()
            if status == "OK" and caps:
                cap_str = caps[0].decode() if isinstance(caps[0], bytes) else str(caps[0])
                return "IDLE" in cap_str.upper()
        except Exception:
            pass
        return False

    def idle_wait(self, timeout: int = 300) -> bool:
        """
        Enter IMAP IDLE mode and wait for new mail notification.

        Args:
            timeout: Maximum seconds to wait in IDLE mode

        Returns:
            True if new mail notification received, False on timeout/error
        """
        if not self._connection:
            return False

        try:
            # Send IDLE command
            tag = self._connection._new_tag().decode()
            self._connection.send(f"{tag} IDLE\r\n".encode())

            # Wait for continuation response
            response = self._connection.readline()
            if not response.startswith(b"+"):
                return False

            # Wait for EXISTS notification or timeout
            sock = self._connection.socket()
            start_time = time.time()

            while time.time() - start_time < timeout:
                # Use select to wait for data with 1 second timeout
                readable, _, _ = select.select([sock], [], [], 1.0)

                if readable:
                    try:
                        line = self._connection.readline()
                        if line:
                            line_str = line.decode("utf-8", errors="replace").upper()
                            # Check for EXISTS (new mail) or RECENT notification
                            if "EXISTS" in line_str or "RECENT" in line_str:
                                # Send DONE to exit IDLE
                                self._connection.send(b"DONE\r\n")
                                # Read the tagged response
                                self._connection.readline()
                                return True
                    except Exception:
                        break

            # Timeout - send DONE to exit IDLE
            self._connection.send(b"DONE\r\n")
            try:
                self._connection.readline()
            except Exception:
                pass
            return False

        except Exception as e:
            print(f"[poller] IDLE error: {e}")
            return False

    def poll_once(self) -> PollResult:
        """
        Poll for new unread emails once.

        Returns:
            PollResult with list of routed messages
        """
        messages = []

        try:
            if not self._connection:
                if not self.connect():
                    return PollResult(False, [], "Failed to connect")

            # Select folder
            folder = self.config.polling.folder
            status, _ = self._connection.select(folder)
            if status != "OK":
                return PollResult(False, [], f"Failed to select folder {folder}")

            # Search for unread messages
            status, data = self._connection.search(None, "UNSEEN")
            if status != "OK":
                return PollResult(False, [], "Failed to search for messages")

            message_ids = data[0].split()
            for msg_id in message_ids:
                try:
                    routed = self._fetch_and_route(msg_id)
                    if routed:
                        # Store IMAP msg_id for later marking as read
                        routed.imap_msg_id = msg_id
                        messages.append(routed)
                        # NOTE: Don't mark as read here - let daemon do it after successful processing
                except Exception as e:
                    print(f"Error processing message {msg_id}: {e}")

            return PollResult(True, messages)

        except Exception as e:
            self._connection = None
            return PollResult(False, [], str(e))

    def mark_as_read(self, msg_id: bytes) -> bool:
        """Mark a message as read by its IMAP message ID."""
        if not self._connection or not msg_id:
            return False
        try:
            if self.config.polling.mark_as_read:
                self._connection.store(msg_id, "+FLAGS", "\\Seen")
                return True
        except Exception as e:
            print(f"[poller] Failed to mark message as read: {e}")
        return False

    def _fetch_and_route(self, msg_id: bytes) -> Optional[RoutedMessage]:
        """Fetch a message and route it."""
        status, data = self._connection.fetch(msg_id, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            return None

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Route the message
        routed = self.router.route_email_message(msg)

        # Extract attachments
        message_id = msg.get("Message-ID", str(msg_id))
        attachments = extract_attachments(msg, message_id)
        routed.attachments = [
            {
                "filename": a.filename,
                "local_path": str(a.local_path),
                "content_type": a.content_type,
                "size": a.size,
            }
            for a in attachments
        ]

        return routed


class ImapPollerDaemon:
    """Background IMAP polling daemon with IDLE support."""

    def __init__(
        self,
        config: MailConfig,
        on_message: Callable[[RoutedMessage], bool],
    ):
        """Initialize the polling daemon.

        Args:
            config: Mail configuration
            on_message: Callback for handling messages. Should return True if
                       message was successfully processed, False otherwise.
        """
        self.config = config
        self.on_message = on_message
        self.poller = ImapPoller(config)
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._use_idle = getattr(config.polling, 'use_idle', True)
        self._idle_timeout = getattr(config.polling, 'idle_timeout', 300)

    def start(self) -> None:
        """Start the polling daemon."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the polling daemon."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.poller.disconnect()

    def is_running(self) -> bool:
        """Check if daemon is running."""
        return self._thread is not None and self._thread.is_alive()

    def _process_messages(self, result: PollResult) -> None:
        """Process polled messages."""
        if result.success:
            for msg in result.messages:
                try:
                    success = self.on_message(msg)
                    # Only mark as read if message was successfully processed
                    if success and msg.imap_msg_id:
                        self.poller.mark_as_read(msg.imap_msg_id)
                except Exception as e:
                    print(f"Error handling message: {e}")
        else:
            if result.error:
                print(f"Poll error: {result.error}")

    def _poll_loop(self) -> None:
        """Main polling loop with IDLE support."""
        interval = self.config.polling.interval_seconds

        # Check if IDLE is supported
        idle_supported = False
        if self._use_idle:
            if self.poller.connect():
                idle_supported = self.poller.supports_idle()
                if idle_supported:
                    print("[maild] IMAP IDLE supported - using real-time notifications")
                else:
                    print(f"[maild] IMAP IDLE not supported - falling back to {interval}s polling")
            else:
                print("[maild] Failed to connect for IDLE check")

        while not self._stop_event.is_set():
            try:
                # First, poll for any existing unread messages
                result = self.poller.poll_once()
                self._process_messages(result)

                if idle_supported and self._use_idle:
                    # Use IDLE mode - wait for new mail notification
                    if not self._stop_event.is_set():
                        # Select folder before IDLE
                        if self.poller._connection:
                            try:
                                self.poller._connection.select(self.config.polling.folder)
                            except Exception:
                                pass

                        # Enter IDLE and wait
                        got_mail = self.poller.idle_wait(timeout=min(self._idle_timeout, 60))
                        if got_mail:
                            # New mail arrived - poll immediately
                            continue
                        # Timeout - loop will re-poll and re-enter IDLE
                else:
                    # Traditional polling - wait for interval
                    self._stop_event.wait(interval)

            except Exception as e:
                print(f"Poll loop error: {e}")
                # On error, disconnect and retry after interval
                self.poller.disconnect()
                self._stop_event.wait(interval)
