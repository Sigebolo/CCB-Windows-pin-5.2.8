"""
Mail service API routes (v2).

Supports pane-based notification system with hooks.
"""

from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import require_auth

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class PaneHookConfig(BaseModel):
    """Pane hook configuration."""
    enabled: bool
    notify_mode: str = "on_completion"


class PaneHookStatus(BaseModel):
    """Pane hook status with runtime info."""
    provider: str
    enabled: bool
    notify_mode: str
    pane_running: bool = False
    last_output_time: Optional[float] = None


class MailConfigResponse(BaseModel):
    """Mail configuration response (v2)."""
    version: int
    enabled: bool
    service_email: Optional[str] = None
    service_provider: Optional[str] = None
    target_email: Optional[str] = None
    pane_hooks: Dict[str, PaneHookConfig] = {}
    notification_prefix: str = "[CCB]"
    polling_interval: int = 30


class MailConfigUpdate(BaseModel):
    """Mail configuration update request."""
    enabled: Optional[bool] = None
    service_email: Optional[str] = None
    service_password: Optional[str] = None
    service_provider: Optional[str] = None
    target_email: Optional[str] = None
    notification_prefix: Optional[str] = None
    polling_interval: Optional[int] = None


class MailTestResult(BaseModel):
    """Mail test result."""
    success: bool
    imap_ok: bool
    imap_message: str
    smtp_ok: bool
    smtp_message: str


class MailStatusResponse(BaseModel):
    """Mail service status (v2)."""
    configured: bool
    enabled: bool
    daemon_running: bool
    daemon_version: int = 1
    service_email: Optional[str] = None
    target_email: Optional[str] = None
    enabled_hooks: List[str] = []


class HookToggleRequest(BaseModel):
    """Hook toggle request."""
    enabled: bool
    notify_mode: Optional[str] = None


# ============================================================================
# Mail Service Endpoints
# ============================================================================

@router.get("/status")
async def get_mail_status(user: dict = Depends(require_auth)) -> MailStatusResponse:
    """Get mail service status."""
    try:
        from mail.config import load_config
        from mail.credentials import has_password
        from mail.daemon import is_daemon_running, get_daemon_status

        config = load_config()

        # Get service email (v1/v2 compatible)
        service_email = ""
        if hasattr(config, 'service_account'):
            service_email = config.service_account.email
        elif hasattr(config, 'account'):
            service_email = config.account.email

        configured = bool(service_email and has_password(service_email))

        # Get daemon status
        daemon_status = get_daemon_status()
        daemon_running = daemon_status.get('running', False)
        daemon_version = daemon_status.get('version', 1)
        enabled_hooks = daemon_status.get('enabled_hooks', [])

        # Get target email and enabled hooks from config
        target_email = getattr(config, 'target_email', '')
        if not enabled_hooks and hasattr(config, 'pane_hooks'):
            enabled_hooks = config.get_enabled_hooks()

        return MailStatusResponse(
            configured=configured,
            enabled=config.enabled,
            daemon_running=daemon_running,
            daemon_version=daemon_version,
            service_email=service_email or None,
            target_email=target_email or None,
            enabled_hooks=enabled_hooks,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_mail_config(user: dict = Depends(require_auth)) -> MailConfigResponse:
    """Get mail configuration."""
    try:
        from mail.config import load_config

        config = load_config()

        # Get service account info (v1/v2 compatible)
        service_email = ""
        service_provider = ""
        if hasattr(config, 'service_account'):
            service_email = config.service_account.email
            service_provider = config.service_account.provider
        elif hasattr(config, 'account'):
            service_email = config.account.email
            service_provider = config.account.provider

        # Get pane hooks
        pane_hooks = {}
        if hasattr(config, 'pane_hooks'):
            for provider, hook in config.pane_hooks.items():
                pane_hooks[provider] = PaneHookConfig(
                    enabled=hook.enabled,
                    notify_mode=hook.notify_mode,
                )

        # Get notification settings
        notification_prefix = "[CCB]"
        if hasattr(config, 'notification'):
            notification_prefix = config.notification.subject_prefix

        return MailConfigResponse(
            version=getattr(config, 'version', 1),
            enabled=config.enabled,
            service_email=service_email or None,
            service_provider=service_provider or None,
            target_email=getattr(config, 'target_email', '') or None,
            pane_hooks=pane_hooks,
            notification_prefix=notification_prefix,
            polling_interval=config.polling.interval_seconds,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_mail_config(
    update: MailConfigUpdate,
    user: dict = Depends(require_auth),
) -> MailConfigResponse:
    """Update mail configuration."""
    try:
        from mail.config import load_config, save_config, ServiceAccountConfig, PROVIDER_PRESETS
        from mail.credentials import store_password

        config = load_config()

        if update.enabled is not None:
            config.enabled = update.enabled

        if update.target_email is not None:
            config.target_email = update.target_email

        if update.service_email is not None:
            config.service_account.email = update.service_email

        if update.service_provider is not None:
            if update.service_provider in PROVIDER_PRESETS:
                email = update.service_email or config.service_account.email
                config.service_account = ServiceAccountConfig.from_preset(
                    update.service_provider, email
                )
            else:
                config.service_account.provider = update.service_provider

        if update.notification_prefix is not None:
            config.notification.subject_prefix = update.notification_prefix

        if update.polling_interval is not None:
            config.polling.interval_seconds = update.polling_interval

        # Store password if provided
        if update.service_password and config.service_account.email:
            store_password(config.service_account.email, update.service_password)

        save_config(config)

        # Return updated config
        return await get_mail_config(user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/toggle")
async def toggle_mail_service(user: dict = Depends(require_auth)) -> dict:
    """Toggle mail service on/off."""
    try:
        from mail.config import load_config, save_config

        config = load_config()
        config.enabled = not config.enabled
        save_config(config)

        return {
            "enabled": config.enabled,
            "message": "Mail service enabled" if config.enabled else "Mail service disabled",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test")
async def test_mail_connection(user: dict = Depends(require_auth)) -> MailTestResult:
    """Test mail connection."""
    try:
        from mail.config import load_config
        from mail.poller import ImapPoller
        from mail.sender import SmtpSender

        config = load_config()

        service_email = ""
        if hasattr(config, 'service_account'):
            service_email = config.service_account.email
        elif hasattr(config, 'account'):
            service_email = config.account.email

        if not service_email:
            raise HTTPException(status_code=400, detail="Mail not configured")

        # Test IMAP
        poller = ImapPoller(config)
        imap_ok, imap_msg = poller.test_connection()

        # Test SMTP
        sender = SmtpSender(config)
        smtp_ok, smtp_msg = sender.test_connection()

        return MailTestResult(
            success=imap_ok and smtp_ok,
            imap_ok=imap_ok,
            imap_message=imap_msg,
            smtp_ok=smtp_ok,
            smtp_message=smtp_msg,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-test")
async def send_test_email(user: dict = Depends(require_auth)) -> dict:
    """Send a test email to target address."""
    try:
        from mail.config import load_config
        from mail.sender import SmtpSender

        config = load_config()

        service_email = ""
        if hasattr(config, 'service_account'):
            service_email = config.service_account.email
        elif hasattr(config, 'account'):
            service_email = config.account.email

        if not service_email:
            raise HTTPException(status_code=400, detail="Mail not configured")

        sender = SmtpSender(config)

        # Send to target email if configured, otherwise to self
        target = getattr(config, 'target_email', '') or service_email

        success, result = sender.send_output(
            to_addr=target,
            provider="test",
            output="This is a test notification from CCB Mail System.\n\nIf you received this, your mail configuration is working correctly.",
        )

        return {
            "success": success,
            "message": f"Test email sent to {target}" if success else f"Failed: {result}",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Pane Hook Endpoints
# ============================================================================

@router.get("/hooks")
async def get_all_hooks(user: dict = Depends(require_auth)) -> List[PaneHookStatus]:
    """Get all pane hook statuses."""
    try:
        from mail.config import load_config, SUPPORTED_PROVIDERS
        from mail.daemon import get_pane_ids

        config = load_config()
        pane_ids = get_pane_ids()

        hooks = []
        for provider in SUPPORTED_PROVIDERS:
            hook_config = config.get_hook(provider) if hasattr(config, 'pane_hooks') else None

            hooks.append(PaneHookStatus(
                provider=provider,
                enabled=hook_config.enabled if hook_config else False,
                notify_mode=hook_config.notify_mode if hook_config else "on_completion",
                pane_running=provider in pane_ids,
            ))

        return hooks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hooks/{provider}")
async def get_hook(provider: str, user: dict = Depends(require_auth)) -> PaneHookStatus:
    """Get a specific pane hook status."""
    try:
        from mail.config import load_config, SUPPORTED_PROVIDERS
        from mail.daemon import get_pane_ids

        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

        config = load_config()
        pane_ids = get_pane_ids()

        hook_config = config.get_hook(provider) if hasattr(config, 'pane_hooks') else None

        return PaneHookStatus(
            provider=provider,
            enabled=hook_config.enabled if hook_config else False,
            notify_mode=hook_config.notify_mode if hook_config else "on_completion",
            pane_running=provider in pane_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/hooks/{provider}")
async def update_hook(
    provider: str,
    update: HookToggleRequest,
    user: dict = Depends(require_auth),
) -> PaneHookStatus:
    """Update a pane hook configuration."""
    try:
        from mail.config import load_config, save_config, SUPPORTED_PROVIDERS
        from mail.daemon import get_pane_ids

        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

        config = load_config()

        if not hasattr(config, 'pane_hooks'):
            raise HTTPException(status_code=400, detail="Config version does not support pane hooks")

        # Update hook
        config.set_hook_enabled(provider, update.enabled)
        if update.notify_mode:
            config.pane_hooks[provider].notify_mode = update.notify_mode

        save_config(config)

        pane_ids = get_pane_ids()

        return PaneHookStatus(
            provider=provider,
            enabled=config.pane_hooks[provider].enabled,
            notify_mode=config.pane_hooks[provider].notify_mode,
            pane_running=provider in pane_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hooks/{provider}/toggle")
async def toggle_hook(provider: str, user: dict = Depends(require_auth)) -> PaneHookStatus:
    """Toggle a pane hook on/off."""
    try:
        from mail.config import load_config, save_config, SUPPORTED_PROVIDERS
        from mail.daemon import get_pane_ids

        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

        config = load_config()

        if not hasattr(config, 'pane_hooks'):
            raise HTTPException(status_code=400, detail="Config version does not support pane hooks")

        # Toggle hook
        current = config.pane_hooks[provider].enabled
        config.set_hook_enabled(provider, not current)
        save_config(config)

        pane_ids = get_pane_ids()

        return PaneHookStatus(
            provider=provider,
            enabled=config.pane_hooks[provider].enabled,
            notify_mode=config.pane_hooks[provider].notify_mode,
            pane_running=provider in pane_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Daemon Control Endpoints
# ============================================================================

@router.post("/daemon/start")
async def start_daemon(user: dict = Depends(require_auth)) -> dict:
    """Start the mail daemon."""
    try:
        from mail.daemon import is_daemon_running, start_daemon as _start_daemon
        import threading

        if is_daemon_running():
            return {"success": False, "message": "Daemon already running"}

        # Start daemon in background thread
        def run_daemon():
            try:
                _start_daemon(foreground=True)
            except Exception:
                pass

        thread = threading.Thread(target=run_daemon, daemon=True)
        thread.start()

        # Wait a bit for daemon to start
        import time
        time.sleep(1)

        if is_daemon_running():
            return {"success": True, "message": "Daemon started"}
        else:
            return {"success": False, "message": "Daemon failed to start"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/daemon/stop")
async def stop_daemon(user: dict = Depends(require_auth)) -> dict:
    """Stop the mail daemon."""
    try:
        from mail.daemon import stop_daemon as _stop_daemon

        success = _stop_daemon()
        return {
            "success": success,
            "message": "Daemon stopped" if success else "Daemon not running",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
