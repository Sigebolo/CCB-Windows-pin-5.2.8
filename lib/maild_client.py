"""
Client for interacting with the mail daemon (maild).
"""

import os
import sys
from typing import Optional

from mail.daemon import (
    is_daemon_running,
    get_daemon_status,
    start_daemon,
    stop_daemon,
    read_daemon_state,
)
from mail.config import load_config, save_config, MailConfig
from mail.credentials import store_password, get_password, has_password
from mail.poller import ImapPoller
from mail.sender import SmtpSender


def check_mail_configured() -> bool:
    """Check if mail is configured."""
    config = load_config()
    return bool(config.account.email and has_password(config.account.email))


def get_mail_status() -> dict:
    """Get mail service status."""
    config = load_config()
    daemon_status = get_daemon_status()

    return {
        "configured": check_mail_configured(),
        "enabled": config.enabled,
        "email": config.account.email,
        "provider": config.account.provider,
        "daemon": daemon_status,
    }


def test_mail_connection() -> dict:
    """Test mail connection (IMAP and SMTP)."""
    config = load_config()
    if not config.account.email:
        return {"success": False, "error": "Mail not configured"}

    results = {"imap": None, "smtp": None}

    # Test IMAP
    poller = ImapPoller(config)
    imap_ok, imap_msg = poller.test_connection()
    results["imap"] = {"success": imap_ok, "message": imap_msg}

    # Test SMTP
    sender = SmtpSender(config)
    smtp_ok, smtp_msg = sender.test_connection()
    results["smtp"] = {"success": smtp_ok, "message": smtp_msg}

    results["success"] = imap_ok and smtp_ok
    return results


def send_test_email() -> dict:
    """Send a test email."""
    config = load_config()
    if not config.account.email:
        return {"success": False, "error": "Mail not configured"}

    sender = SmtpSender(config)
    success, result = sender.send_test_email()

    return {
        "success": success,
        "message": result if success else f"Failed: {result}",
    }


def start_mail_service(foreground: bool = False) -> bool:
    """Start the mail service."""
    if not check_mail_configured():
        print("Mail service not configured. Run 'ccb mail setup' first.")
        return False

    config = load_config()
    if not config.enabled:
        config.enabled = True
        save_config(config)

    start_daemon(foreground=foreground)
    return True


def stop_mail_service() -> bool:
    """Stop the mail service."""
    return stop_daemon()
