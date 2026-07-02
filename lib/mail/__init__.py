"""
CCB Mail System - Email-based AI provider interaction.

This module provides email integration for CCB, allowing users to interact
with AI providers (Claude, Codex, Gemini, etc.) via email.

Version 3: ASK-based mail system
- Routes emails to ASK system via provider prefix in body
- Replies via ccb-completion-hook with CCB_CALLER=email
- No pane monitoring required

Key components:
- config: Configuration management (v3 with ASK settings)
- credentials: Secure credential storage via keyring
- body_parser: Parse provider prefix from email body
- ask_handler: ASK system integration
- threads: Thread-ID to Session-ID mapping
- poller: IMAP polling
- sender: SMTP sending
- daemon: maild daemon process
"""

__version__ = "3.0.0"

from .config import (
    MailConfig,
    MailConfigV3,
    MailConfigV2,
    ServiceAccountConfig,
    AccountConfig,  # Alias for backward compatibility
    PaneHookConfig,
    NotificationConfig,
    PollingConfig,
    ImapConfig,
    SmtpConfig,
    SUPPORTED_PROVIDERS,
    PROVIDER_PRESETS,
    CURRENT_CONFIG_VERSION,
    load_config,
    save_config,
    validate_config,
    is_configured,
    get_config_dir,
    ensure_config_dir,
)

from .credentials import (
    store_password,
    get_password,
    delete_password,
    has_password,
    is_keyring_available,
)

from .body_parser import (
    BodyParser,
    ParsedMessage,
)

from .ask_handler import (
    AskHandler,
    AskResult,
    load_email_context,
    cleanup_email_context,
)

from .filters import (
    filter_outgoing,
    filter_incoming,
    clean_email_body,
    FilterResult,
)

from .sender import SmtpSender

from .daemon import (
    MailDaemon,
    DaemonState,
    is_daemon_running,
    get_daemon_status,
    start_daemon,
    stop_daemon,
)

__all__ = [
    # Config classes
    "MailConfig",
    "MailConfigV3",
    "MailConfigV2",
    "ServiceAccountConfig",
    "AccountConfig",
    "PaneHookConfig",
    "NotificationConfig",
    "PollingConfig",
    "ImapConfig",
    "SmtpConfig",
    # Constants
    "SUPPORTED_PROVIDERS",
    "PROVIDER_PRESETS",
    "CURRENT_CONFIG_VERSION",
    # Config functions
    "load_config",
    "save_config",
    "validate_config",
    "is_configured",
    "get_config_dir",
    "ensure_config_dir",
    # Credential functions
    "store_password",
    "get_password",
    "delete_password",
    "has_password",
    "is_keyring_available",
    # Body parser (v3)
    "BodyParser",
    "ParsedMessage",
    # ASK handler (v3)
    "AskHandler",
    "AskResult",
    "load_email_context",
    "cleanup_email_context",
    # Filters
    "filter_outgoing",
    "filter_incoming",
    "clean_email_body",
    "FilterResult",
    # Sender
    "SmtpSender",
    # Daemon
    "MailDaemon",
    "DaemonState",
    "is_daemon_running",
    "get_daemon_status",
    "start_daemon",
    "stop_daemon",
]
