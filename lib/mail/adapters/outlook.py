"""
Outlook adapter for CCB Mail.
"""

from . import BaseMailAdapter, ProviderPreset


class OutlookAdapter(BaseMailAdapter):
    """Outlook/Microsoft 365 mail provider adapter."""

    @property
    def preset(self) -> ProviderPreset:
        return ProviderPreset(
            name="outlook",
            display_name="Outlook / Microsoft 365",
            imap_host="outlook.office365.com",
            imap_port=993,
            imap_ssl=True,
            smtp_host="smtp.office365.com",
            smtp_port=587,
            smtp_ssl=False,
            smtp_starttls=True,
            help_url="https://support.microsoft.com/en-us/account-billing/using-app-passwords-with-apps-that-don-t-support-two-step-verification-5896ed9b-4263-e681-128a-a6f2979a7944",
            notes="May require App Password if 2FA is enabled",
        )

    def get_auth_instructions(self) -> str:
        return """Outlook Setup Instructions:

For personal accounts (outlook.com, hotmail.com):
1. Go to: https://account.live.com/proofs/manage
2. Enable Two-step verification
3. Go to: https://account.live.com/proofs/AppPassword
4. Create an app password
5. Use this app password in CCB

For Microsoft 365 / Work accounts:
- Contact your IT administrator
- IMAP/SMTP access may need to be enabled
- App passwords may be required"""

    def validate_email(self, email: str) -> bool:
        valid_domains = [
            "@outlook.com", "@hotmail.com", "@live.com",
            "@msn.com", "@outlook.co", "@hotmail.co"
        ]
        return any(email.endswith(d) or d.replace("@", "@") in email for d in valid_domains)
