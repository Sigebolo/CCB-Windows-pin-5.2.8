from __future__ import annotations

from dataclasses import dataclass

from ccb_protocol import (
    DONE_PREFIX,
    REQ_ID_PREFIX,
    is_done_text,
    make_req_id,
    strip_done_text,
)


def wrap_grok_prompt(message: str, req_id: str) -> str:
    message = (message or "").rstrip()
    return (
        f"{REQ_ID_PREFIX} {req_id}\n\n"
        f"{message}\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Read the request carefully and understand what is being asked.\n"
        "2. Think through your approach before responding. Include your reasoning, "
        "trade-offs considered, and key decisions in your reply.\n"
        "3. Provide a COMPLETE response — do not truncate or leave partial output.\n"
        "4. Your response MUST be self-contained and actionable.\n"
        "5. End your reply with this exact final line (verbatim, on its own line):\n"
        f"{DONE_PREFIX} {req_id}\n"
    )


@dataclass(frozen=True)
class XaskdRequest:
    client_id: str
    work_dir: str
    timeout_s: float
    quiet: bool
    message: str
    output_path: str | None = None
    req_id: str | None = None
    caller: str = "claude"


@dataclass(frozen=True)
class XaskdResult:
    exit_code: int
    reply: str
    req_id: str
    session_key: str
    done_seen: bool
    done_ms: int | None = None
