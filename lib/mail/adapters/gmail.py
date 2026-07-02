"""
Gmail adapter for CCB Mail.
"""

from . import BaseMailAdapter, ProviderPreset


class GmailAdapter(BaseMailAdapter):
    """Gmail mail provider adapter."""

    @property
    def preset(self) -> ProviderPreset:
        return ProviderPreset(
            name="gmail",
            display_name="Gmail",
            imap_host="imap.gmail.com",
            imap_port=993,
            imap_ssl=True,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_ssl=False,
            smtp_starttls=True,
            help_url="https://support.google.com/accounts/answer/185833",
            notes="Requires App Password (2FA must be enabled)",
        )

    def get_auth_instructions(self) -> str:
        return """Gmail Setup Instructions:

1. Enable 2-Factor Authentication on your Google account
2. Go to: https://myaccount.google.com/apppasswords
3. Select "Mail" and your device
4. Click "Generate"
5. Copy the 16-character app password
6. Use this app password (not your regular password) in CCB

Note: App passwords are only available with 2FA enabled."""

    def validate_email(self, email: str) -> bool:
        return email.endswith("@gmail.com") or email.endswith("@googlemail.com")
