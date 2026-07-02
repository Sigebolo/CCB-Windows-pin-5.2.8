"""
Attachment handling for CCB Mail.

Downloads and caches email attachments for processing by AI providers.
"""

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from email.message import EmailMessage

# Default cache directory
DEFAULT_CACHE_DIR = Path.home() / ".ccb" / "cache" / "mail_attachments"
# Clean up attachments older than 24 hours
ATTACHMENT_TTL_SECONDS = 24 * 60 * 60
# Maximum attachment size (10MB)
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024


@dataclass
class CachedAttachment:
    """Information about a cached attachment."""
    filename: str
    local_path: Path
    content_type: str
    size: int
    message_id: str


def get_cache_dir() -> Path:
    """Get the attachment cache directory."""
    cache_dir = Path(os.environ.get("CCB_MAIL_CACHE_DIR", DEFAULT_CACHE_DIR))
    return cache_dir


def ensure_cache_dir() -> Path:
    """Ensure the cache directory exists."""
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_message_cache_dir(message_id: str) -> Path:
    """Get cache directory for a specific message."""
    # Hash the message ID to create a safe directory name
    safe_id = hashlib.sha256(message_id.encode()).hexdigest()[:16]
    return get_cache_dir() / safe_id


def save_attachment(
    message_id: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> Optional[CachedAttachment]:
    """
    Save an attachment to the cache.

    Args:
        message_id: Email Message-ID
        filename: Original filename
        content: Attachment content bytes
        content_type: MIME content type

    Returns:
        CachedAttachment info or None if failed
    """
    if len(content) > MAX_ATTACHMENT_SIZE:
        print(f"Warning: Attachment {filename} exceeds size limit, skipping")
        return None

    ensure_cache_dir()
    msg_dir = get_message_cache_dir(message_id)
    msg_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
    if not safe_filename:
        safe_filename = "attachment"

    local_path = msg_dir / safe_filename

    # Handle duplicate filenames
    counter = 1
    while local_path.exists():
        name, ext = os.path.splitext(safe_filename)
        local_path = msg_dir / f"{name}_{counter}{ext}"
        counter += 1

    try:
        with open(local_path, "wb") as f:
            f.write(content)
        local_path.chmod(0o600)

        return CachedAttachment(
            filename=filename,
            local_path=local_path,
            content_type=content_type,
            size=len(content),
            message_id=message_id,
        )
    except IOError as e:
        print(f"Error saving attachment {filename}: {e}")
        return None


def extract_attachments(msg: EmailMessage, message_id: str, include_inline: bool = True) -> List[CachedAttachment]:
    """
    Extract and cache all attachments from an email message.

    Args:
        msg: EmailMessage object
        message_id: Email Message-ID
        include_inline: Also extract inline images (default True)

    Returns:
        List of CachedAttachment objects
    """
    attachments = []

    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        content_type = part.get_content_type()

        # Check if it's an attachment
        is_attachment = "attachment" in content_disposition

        # Check if it's an inline image
        is_inline_image = (
            include_inline
            and content_type.startswith("image/")
            and ("inline" in content_disposition or not content_disposition)
        )

        if not is_attachment and not is_inline_image:
            continue

        filename = part.get_filename()
        if not filename:
            # Generate filename for inline images
            if is_inline_image:
                ext = content_type.split("/")[-1]
                if ext == "jpeg":
                    ext = "jpg"
                filename = f"image.{ext}"
            else:
                continue

        content = part.get_payload(decode=True)
        if not content:
            continue

        cached = save_attachment(message_id, filename, content, content_type)
        if cached:
            attachments.append(cached)

    return attachments


def get_cached_attachments(message_id: str) -> List[CachedAttachment]:
    """Get all cached attachments for a message."""
    msg_dir = get_message_cache_dir(message_id)
    if not msg_dir.exists():
        return []

    attachments = []
    for path in msg_dir.iterdir():
        if path.is_file():
            attachments.append(CachedAttachment(
                filename=path.name,
                local_path=path,
                content_type="application/octet-stream",
                size=path.stat().st_size,
                message_id=message_id,
            ))
    return attachments


def delete_cached_attachments(message_id: str) -> bool:
    """Delete all cached attachments for a message."""
    msg_dir = get_message_cache_dir(message_id)
    if msg_dir.exists():
        try:
            shutil.rmtree(msg_dir)
            return True
        except IOError as e:
            print(f"Error deleting attachments for {message_id}: {e}")
    return False


def cleanup_old_attachments(ttl_seconds: float = ATTACHMENT_TTL_SECONDS) -> int:
    """
    Remove attachment directories older than TTL.

    Returns:
        Number of directories removed
    """
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return 0

    now = time.time()
    removed = 0

    for msg_dir in cache_dir.iterdir():
        if not msg_dir.is_dir():
            continue

        try:
            mtime = msg_dir.stat().st_mtime
            if now - mtime > ttl_seconds:
                shutil.rmtree(msg_dir)
                removed += 1
        except IOError:
            pass

    return removed
