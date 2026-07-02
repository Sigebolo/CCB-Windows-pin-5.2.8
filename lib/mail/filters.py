"""
Content filtering for CCB Mail.

Filters email content for security and cleanliness.
"""

import re
from typing import Tuple, List, Optional
from dataclasses import dataclass


@dataclass
class FilterResult:
    """Result of content filtering."""
    passed: bool
    content: str
    warnings: List[str]
    blocked_reason: Optional[str] = None


# Patterns to remove from outgoing emails
OUTGOING_STRIP_PATTERNS = [
    # ANSI escape codes
    r'\x1b\[[0-9;]*[a-zA-Z]',
    # Control characters (except newline, tab)
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',
    # Very long lines (truncate to 500 chars)
    # Handled separately
]

# Patterns to remove from incoming emails
INCOMING_STRIP_PATTERNS = [
    # Email signatures
    r'\n--\s*\n.*$',
    r'\n━+\n.*$',
    # Quoted text
    r'\n>.*$',
    # "On ... wrote:" patterns
    r'\nOn .+ wrote:\s*$',
    r'\n.*于.*写道：\s*$',
    # Sent from patterns
    r'\nSent from .*$',
    r'\n发自.*$',
    # CCB signature
    r'\n---\nSent via CCB.*$',
]

# Dangerous patterns to block
DANGEROUS_PATTERNS = [
    # Shell injection attempts
    (r';\s*rm\s+-rf', 'Potential shell injection'),
    (r'\$\(.*\)', 'Command substitution'),
    (r'`.*`', 'Backtick command'),
    # SQL injection
    (r"'\s*OR\s+'1'\s*=\s*'1", 'SQL injection attempt'),
    (r';\s*DROP\s+TABLE', 'SQL injection attempt'),
]

# Maximum content length
MAX_OUTGOING_LENGTH = 50000
MAX_INCOMING_LENGTH = 10000


def filter_outgoing(content: str, max_length: int = MAX_OUTGOING_LENGTH) -> FilterResult:
    """
    Filter content for outgoing emails (pane output -> email).

    Args:
        content: Raw pane output
        max_length: Maximum content length

    Returns:
        FilterResult with cleaned content
    """
    warnings = []
    filtered = content

    # Remove ANSI escape codes
    filtered = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', filtered)

    # Remove control characters
    filtered = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', filtered)

    # Truncate very long lines
    lines = filtered.split('\n')
    truncated_lines = []
    for line in lines:
        if len(line) > 500:
            truncated_lines.append(line[:500] + '...(truncated)')
            warnings.append('Some lines were truncated')
        else:
            truncated_lines.append(line)
    filtered = '\n'.join(truncated_lines)

    # Truncate total length
    if len(filtered) > max_length:
        filtered = filtered[:max_length] + '\n\n...(content truncated)'
        warnings.append(f'Content truncated to {max_length} characters')

    # Remove duplicate warnings
    warnings = list(set(warnings))

    return FilterResult(
        passed=True,
        content=filtered,
        warnings=warnings,
    )


def filter_incoming(content: str, max_length: int = MAX_INCOMING_LENGTH) -> FilterResult:
    """
    Filter content for incoming emails (email reply -> pane input).

    Args:
        content: Email body text
        max_length: Maximum content length

    Returns:
        FilterResult with cleaned content
    """
    warnings = []
    filtered = content

    # Check for dangerous patterns
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, filtered, re.IGNORECASE):
            return FilterResult(
                passed=False,
                content='',
                warnings=[],
                blocked_reason=reason,
            )

    # Remove quoted text and signatures
    for pattern in INCOMING_STRIP_PATTERNS:
        filtered = re.sub(pattern, '', filtered, flags=re.MULTILINE | re.DOTALL)

    # Strip whitespace
    filtered = filtered.strip()

    # Truncate if too long
    if len(filtered) > max_length:
        filtered = filtered[:max_length]
        warnings.append(f'Content truncated to {max_length} characters')

    # Check if content is empty after filtering
    if not filtered:
        return FilterResult(
            passed=False,
            content='',
            warnings=[],
            blocked_reason='Empty content after filtering',
        )

    return FilterResult(
        passed=True,
        content=filtered,
        warnings=warnings,
    )


def clean_email_body(body: str) -> str:
    """
    Clean email body by removing quoted text and signatures.

    This is a simpler version for quick cleanup.
    """
    lines = body.split('\n')
    clean_lines = []

    for line in lines:
        stripped = line.strip()
        # Stop at quoted text
        if line.startswith('>'):
            break
        # Stop at signature markers
        if stripped in ('--', '---', '━' * 10):
            break
        # Stop at "On ... wrote:" patterns
        if re.match(r'^On .+ wrote:$', stripped):
            break
        if '写道：' in line or ('于' in line and '写道' in line):
            break
        # Stop at CCB signature
        if 'Sent via CCB' in line:
            break
        # Stop at replied message marker (126/QQ mail format)
        if stripped.startswith('---- Replied Message ----'):
            break
        if stripped.startswith('----') and 'Message' in stripped:
            break
        # Stop at Chinese replied message marker (126/QQ mail format)
        if stripped.startswith('----') and '回复' in stripped:
            break
        if stripped.startswith('----') and '原邮件' in stripped:
            break
        # Stop at forwarded message marker
        if stripped.startswith('---- Forwarded Message ----'):
            break
        # Stop at table-like reply headers (English and Chinese)
        if stripped.startswith('| From |') or stripped.startswith('| Date |'):
            break
        if stripped.startswith('| 发件人 |') or stripped.startswith('| 日期 |'):
            break

        clean_lines.append(line)

    return '\n'.join(clean_lines).strip()


def sanitize_subject(subject: str, max_length: int = 100) -> str:
    """Sanitize email subject line."""
    # Remove newlines
    subject = subject.replace('\n', ' ').replace('\r', '')
    # Truncate
    if len(subject) > max_length:
        subject = subject[:max_length-3] + '...'
    return subject


def _looks_like_diff(lines: List[str]) -> bool:
    for line in lines:
        if line.startswith("diff --git "):
            return True
        if line.startswith("--- ") or line.startswith("+++ "):
            return True
        if line.startswith("@@ "):
            return True
    return False


def escape_signature_separators(content: str) -> str:
    """
    Escape signature-like separators that cause some email clients to trim/hide content.

    If the content looks like a diff, indent all lines to avoid signature detection.
    Otherwise, only escape lines that are pure separator markers or diff headers.
    """
    if not content:
        return content

    lines = content.splitlines()
    if not lines:
        return content

    if _looks_like_diff(lines):
        return "\n".join(f" {line}" for line in lines)

    escaped: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in ("--", "---") or stripped.startswith("--- ") or stripped.startswith("+++ "):
            escaped.append(f" {line}")
        else:
            escaped.append(line)
    return "\n".join(escaped)
