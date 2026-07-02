"""
Secure credential storage for CCB Mail using system keyring.

Credentials are stored in the system keyring (e.g., GNOME Keyring, macOS Keychain)
for security. Falls back to encrypted file storage if keyring is unavailable.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

# Service name for keyring
KEYRING_SERVICE = "ccb-mail"
KEYRING_USERNAME = "mail-credentials"

# Fallback encrypted file
FALLBACK_CREDS_FILE = "credentials.enc"


def _get_keyring():
    """Get keyring module if available."""
    try:
        import keyring
        # Test if keyring backend is functional
        keyring.get_keyring()
        return keyring
    except (ImportError, Exception):
        return None


def _get_fallback_path() -> Path:
    """Get path for fallback credentials file."""
    from .config import get_config_dir
    return get_config_dir() / FALLBACK_CREDS_FILE


def _derive_key(salt: bytes) -> bytes:
    """Derive encryption key from machine-specific data."""
    # Use machine ID + username as key material
    machine_id = ""
    try:
        with open("/etc/machine-id", "r") as f:
            machine_id = f.read().strip()
    except FileNotFoundError:
        pass

    key_material = f"{machine_id}:{os.getlogin()}:{KEYRING_SERVICE}"
    return hashlib.pbkdf2_hmac("sha256", key_material.encode(), salt, 100000)


def _simple_encrypt(data: str) -> str:
    """Simple XOR-based encryption for fallback storage."""
    salt = os.urandom(16)
    key = _derive_key(salt)
    data_bytes = data.encode("utf-8")
    # XOR encryption
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data_bytes))
    # Combine salt + encrypted data
    combined = salt + encrypted
    return base64.b64encode(combined).decode("ascii")


def _simple_decrypt(encrypted_data: str) -> str:
    """Decrypt data encrypted with _simple_encrypt."""
    combined = base64.b64decode(encrypted_data.encode("ascii"))
    salt = combined[:16]
    encrypted = combined[16:]
    key = _derive_key(salt)
    # XOR decryption
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
    return decrypted.decode("utf-8")


def store_password(email: str, password: str) -> bool:
    """
    Store email password securely.

    Args:
        email: Email address (used as identifier)
        password: Password or app-specific password

    Returns:
        True if stored successfully, False otherwise
    """
    keyring = _get_keyring()
    if keyring:
        try:
            keyring.set_password(KEYRING_SERVICE, email, password)
            return True
        except Exception as e:
            print(f"Warning: Keyring storage failed: {e}")

    # Fallback to encrypted file
    try:
        fallback_path = _get_fallback_path()
        creds = {}
        if fallback_path.exists():
            try:
                with open(fallback_path, "r") as f:
                    encrypted = f.read()
                creds = json.loads(_simple_decrypt(encrypted))
            except Exception:
                pass

        creds[email] = password
        encrypted = _simple_encrypt(json.dumps(creds))

        with open(fallback_path, "w") as f:
            f.write(encrypted)
        fallback_path.chmod(0o600)
        return True
    except Exception as e:
        print(f"Error storing credentials: {e}")
        return False


def get_password(email: str) -> Optional[str]:
    """
    Retrieve stored password for email.

    Args:
        email: Email address

    Returns:
        Password if found, None otherwise
    """
    keyring = _get_keyring()
    if keyring:
        try:
            password = keyring.get_password(KEYRING_SERVICE, email)
            if password:
                return password
        except Exception:
            pass

    # Try fallback file
    try:
        fallback_path = _get_fallback_path()
        if fallback_path.exists():
            with open(fallback_path, "r") as f:
                encrypted = f.read()
            creds = json.loads(_simple_decrypt(encrypted))
            return creds.get(email)
    except Exception:
        pass

    return None


def delete_password(email: str) -> bool:
    """
    Delete stored password for email.

    Args:
        email: Email address

    Returns:
        True if deleted successfully
    """
    deleted = False

    keyring = _get_keyring()
    if keyring:
        try:
            keyring.delete_password(KEYRING_SERVICE, email)
            deleted = True
        except Exception:
            pass

    # Also try to remove from fallback
    try:
        fallback_path = _get_fallback_path()
        if fallback_path.exists():
            with open(fallback_path, "r") as f:
                encrypted = f.read()
            creds = json.loads(_simple_decrypt(encrypted))
            if email in creds:
                del creds[email]
                encrypted = _simple_encrypt(json.dumps(creds))
                with open(fallback_path, "w") as f:
                    f.write(encrypted)
                deleted = True
    except Exception:
        pass

    return deleted


def has_password(email: str) -> bool:
    """Check if password is stored for email."""
    return get_password(email) is not None


def is_keyring_available() -> bool:
    """Check if system keyring is available."""
    return _get_keyring() is not None
