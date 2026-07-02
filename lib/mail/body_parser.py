"""
Body parser for CCB Mail v3.

Parses provider prefix from email body text.
Example: "codex hello" -> ("codex", "hello")
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedMessage:
    """Result of parsing an email body."""
    provider: Optional[str]  # Extracted provider or None
    message: str             # Message with prefix removed


class BodyParser:
    """Parses provider prefix from email body."""

    # Default pattern: provider name at start (first word)
    DEFAULT_PATTERN = r"^(\w+)"

    def __init__(
        self,
        pattern: str = DEFAULT_PATTERN,
        case_insensitive: bool = True,
        valid_providers: Optional[list] = None,
    ):
        """
        Initialize body parser.

        Args:
            pattern: Regex pattern to match provider prefix.
                     Must have one capture group for provider name.
            case_insensitive: Whether to match case-insensitively.
            valid_providers: List of valid provider names.
                            If None, accepts any matched provider.
        """
        self.pattern = pattern
        self.case_insensitive = case_insensitive
        self.valid_providers = valid_providers
        flags = re.IGNORECASE if case_insensitive else 0
        self._regex = re.compile(pattern, flags)

    def parse(self, body: str) -> ParsedMessage:
        """
        Parse email body to extract provider prefix.

        Args:
            body: Email body text

        Returns:
            ParsedMessage with provider (if found) and cleaned message

        Examples:
            "CLAUDE: fix the bug" -> ("claude", "fix the bug")
            "codex: analyze"      -> ("codex", "analyze")
            "just a message"      -> (None, "just a message")
        """
        body = body.strip()
        if not body:
            return ParsedMessage(provider=None, message="")

        match = self._regex.match(body)
        if not match:
            return ParsedMessage(provider=None, message=body)

        provider = match.group(1).lower()

        # Validate provider if list provided
        if self.valid_providers and provider not in self.valid_providers:
            return ParsedMessage(provider=None, message=body)

        # Remove prefix from message
        message = body[match.end():].strip()
        return ParsedMessage(provider=provider, message=message)

    def parse_multiline(self, body: str) -> ParsedMessage:
        """
        Parse only the first line for provider prefix.

        Useful when email body has multiple lines and provider
        prefix is only on the first line.

        Args:
            body: Email body text

        Returns:
            ParsedMessage with provider and full message (minus prefix)
        """
        body = body.strip()
        if not body:
            return ParsedMessage(provider=None, message="")

        lines = body.split('\n', 1)
        first_line = lines[0].strip()

        match = self._regex.match(first_line)
        if not match:
            return ParsedMessage(provider=None, message=body)

        provider = match.group(1).lower()

        # Validate provider if list provided
        if self.valid_providers and provider not in self.valid_providers:
            return ParsedMessage(provider=None, message=body)

        # Remove prefix from first line, keep rest
        first_line_remainder = first_line[match.end():].strip()
        if len(lines) > 1:
            message = first_line_remainder + '\n' + lines[1]
        else:
            message = first_line_remainder

        return ParsedMessage(provider=provider, message=message.strip())
