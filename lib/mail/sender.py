"""
SMTP sending for CCB Mail.

Sends email replies from AI providers back to users.
Supports v2 config with service_account and pane output notifications.
"""

import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from typing import Optional, List, Callable, TypeVar

from .config import MailConfig, SmtpConfig
from .credentials import get_password
from .filters import filter_outgoing, sanitize_subject


T = TypeVar('T')


def _retry_on_failure(
    func: Callable[[], T],
    max_retries: int = 3,
    retry_delay: float = 2.0,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> T:
    """
    Retry a function on failure with exponential backoff.

    Args:
        func: Function to retry
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries (doubles each retry)
        on_retry: Optional callback called before each retry with (attempt, exception)

    Returns:
        Result of the function

    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    delay = retry_delay

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                if on_retry:
                    on_retry(attempt + 1, e)
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise

    raise last_exception  # Should never reach here


class SmtpSender:
    """SMTP email sender."""

    def __init__(self, config: MailConfig):
        self.config = config
        # Support both v1 (account) and v2 (service_account) config
        if hasattr(config, 'service_account'):
            self.smtp_config = config.service_account.smtp
            self.email_addr = config.service_account.email
        else:
            self.smtp_config = config.account.smtp
            self.email_addr = config.account.email
        self._connection: Optional[smtplib.SMTP] = None

    def _create_connection(self) -> smtplib.SMTP:
        """Create SMTP connection."""
        if self.smtp_config.ssl:
            context = ssl.create_default_context()
            conn = smtplib.SMTP_SSL(
                self.smtp_config.host,
                self.smtp_config.port,
                context=context,
                timeout=30,
            )
        else:
            conn = smtplib.SMTP(self.smtp_config.host, self.smtp_config.port, timeout=30)
            conn.ehlo()
            if self.smtp_config.starttls:
                context = ssl.create_default_context()
                conn.starttls(context=context)
                conn.ehlo()

        return conn

    def connect(self) -> bool:
        """Connect and authenticate to SMTP server."""
        try:
            password = get_password(self.email_addr)
            if not password:
                raise ValueError(f"No password stored for {self.email_addr}")

            self._connection = self._create_connection()
            self._connection.login(self.email_addr, password)
            return True
        except Exception as e:
            print(f"SMTP connection failed: {e}")
            self._connection = None
            return False

    def disconnect(self) -> None:
        """Disconnect from SMTP server."""
        if self._connection:
            try:
                self._connection.quit()
            except Exception:
                pass
            self._connection = None

    def test_connection(self) -> tuple[bool, str]:
        """
        Test SMTP connection.

        Returns:
            Tuple of (success, message)
        """
        try:
            if self.connect():
                self.disconnect()
                return True, "SMTP connection successful"
            return False, "SMTP authentication failed"
        except Exception as e:
            return False, f"SMTP connection error: {e}"

    def send_reply(
        self,
        to_addr: str,
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        provider: Optional[str] = None,
        max_retries: int = 3,
    ) -> tuple[bool, str]:
        """
        Send an email reply with automatic retry on failure.

        Args:
            to_addr: Recipient email address
            subject: Email subject
            body: Email body text
            in_reply_to: Message-ID being replied to
            references: References header for threading
            provider: AI provider name (for signature)
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            Tuple of (success, message_id or error)
        """
        def _send() -> tuple[bool, str]:
            if not self._connection:
                if not self.connect():
                    raise ConnectionError("Failed to connect to SMTP")

            # Create message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.email_addr
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid()

            # Threading headers
            if in_reply_to:
                msg["In-Reply-To"] = in_reply_to
            if references:
                msg["References"] = references

            # Add provider signature
            body_with_sig = body
            if provider:
                body_with_sig = f"{body}\n\n---\nSent via CCB ({provider})"

            # Add body
            text_part = MIMEText(body_with_sig, "plain", "utf-8")
            msg.attach(text_part)

            # Send
            self._connection.send_message(msg)
            return True, msg["Message-ID"]

        def _on_retry(attempt: int, e: Exception) -> None:
            print(f"[smtp] Retry {attempt}/{max_retries} after error: {e}")
            # Reset connection for retry
            self._connection = None

        try:
            return _retry_on_failure(_send, max_retries=max_retries, retry_delay=2.0, on_retry=_on_retry)
        except Exception as e:
            self._connection = None
            return False, str(e)

    def send_test_email(self) -> tuple[bool, str]:
        """
        Send a test email to self.

        Returns:
            Tuple of (success, message)
        """
        return self.send_reply(
            to_addr=self.email_addr,
            subject="[CCB] Test Email",
            body="This is a test email from CCB Mail System.\n\nIf you received this, your email configuration is working correctly.",
            provider="test",
        )

    def send_output(
        self,
        to_addr: str,
        provider: str,
        output: str,
        thread_id: Optional[str] = None,
        work_dir: Optional[str] = None,
        max_retries: int = 3,
    ) -> tuple[bool, str]:
        """
        Send pane output notification email with automatic retry.

        Args:
            to_addr: Recipient email address
            provider: AI provider name (claude, codex, etc.)
            output: Pane output text
            thread_id: Optional thread ID for email threading
            work_dir: Optional working directory for context
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            Tuple of (success, message_id or error)
        """
        import os

        # Filter output content
        filter_result = filter_outgoing(output)
        output = filter_result.content

        # Generate thread ID if not provided
        if not thread_id:
            thread_id = f"ccb-{provider}-{int(time.time())}"

        # Build subject with work dir hint
        subject_prefix = self.config.notification.subject_prefix if hasattr(self.config, 'notification') else "[CCB]"

        # Add work dir to subject if provided
        if work_dir:
            # Use last directory name for brevity
            dir_name = os.path.basename(work_dir.rstrip('/'))
            subject = f"{subject_prefix} {provider.capitalize()} @ {dir_name}"
        else:
            subject = f"{subject_prefix} {provider.capitalize()} Output"

        # Sanitize subject
        subject = sanitize_subject(subject)

        # Truncate output if too long
        max_length = self.config.notification.max_email_length if hasattr(self.config, 'notification') else 10000
        if len(output) > max_length:
            output = output[:max_length] + "\n\n... (truncated)"

        # Build body with work dir info
        separator = "━" * 40
        work_dir_line = f"\n工作目录: {work_dir}" if work_dir else ""
        body = f"""{separator}
 {provider.capitalize()} Pane Output{work_dir_line}
{separator}

{output}

{separator}
回复此邮件即可向 {provider.capitalize()} 发送指令
Reply to this email to send commands to {provider.capitalize()}
{separator}"""

        def _send() -> tuple[bool, str]:
            if not self._connection:
                if not self.connect():
                    raise ConnectionError("Failed to connect to SMTP")

            # Create message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.email_addr
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = f"<{thread_id}@ccb>"
            msg["X-CCB-Thread-ID"] = thread_id
            msg["X-CCB-Provider"] = provider

            # Add body
            text_part = MIMEText(body, "plain", "utf-8")
            msg.attach(text_part)

            # Send
            self._connection.send_message(msg)
            return True, msg["Message-ID"]

        def _on_retry(attempt: int, e: Exception) -> None:
            print(f"[smtp] Retry {attempt}/{max_retries} after error: {e}")
            # Reset connection for retry
            self._connection = None

        try:
            return _retry_on_failure(_send, max_retries=max_retries, retry_delay=2.0, on_retry=_on_retry)
        except Exception as e:
            self._connection = None
            return False, str(e)
