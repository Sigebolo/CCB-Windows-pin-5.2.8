"""
QQ Mail adapter for CCB Mail.
"""

from . import BaseMailAdapter, ProviderPreset


class QQMailAdapter(BaseMailAdapter):
    """QQ Mail provider adapter."""

    @property
    def preset(self) -> ProviderPreset:
        return ProviderPreset(
            name="qq",
            display_name="QQ 邮箱",
            imap_host="imap.qq.com",
            imap_port=993,
            imap_ssl=True,
            smtp_host="smtp.qq.com",
            smtp_port=465,
            smtp_ssl=True,
            smtp_starttls=False,
            help_url="https://service.mail.qq.com/cgi-bin/help?subtype=1&&id=28&&no=1001256",
            notes="需要开启IMAP服务并获取授权码",
        )

    def get_auth_instructions(self) -> str:
        return """QQ邮箱设置说明:

1. 登录 QQ 邮箱网页版
2. 进入 设置 -> 账户
3. 找到 "POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务"
4. 开启 "IMAP/SMTP服务"
5. 按提示发送短信验证
6. 获取授权码（16位）
7. 在 CCB 中使用此授权码作为密码

注意: 授权码不是QQ密码，请妥善保管"""

    def validate_email(self, email: str) -> bool:
        return email.endswith("@qq.com") or email.endswith("@foxmail.com")
