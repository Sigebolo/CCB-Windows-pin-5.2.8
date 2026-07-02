"""
Base adapter for mail providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class ProviderPreset:
    """Mail provider preset configuration."""
    name: str
    display_name: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    smtp_starttls: bool
    help_url: str = ""
    notes: str = ""


class BaseMailAdapter(ABC):
    """Base class for mail provider adapters."""

    @property
    @abstractmethod
    def preset(self) -> ProviderPreset:
        """Get the provider preset configuration."""
        pass

    @abstractmethod
    def get_auth_instructions(self) -> str:
        """Get instructions for setting up authentication."""
        pass

    def validate_email(self, email: str) -> bool:
        """Validate email format for this provider."""
        return "@" in email
