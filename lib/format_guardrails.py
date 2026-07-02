from __future__ import annotations

import re


def wants_code_fences(message: str) -> bool:
    msg = (message or "").lower()
    if "```" in (message or ""):
        return True
    if "code block" in msg or "fenced" in msg or "fence" in msg:
        return True
    if "代码块" in (message or ""):
        return True
    if "多行代码" in (message or "") or "multi-line code" in msg:
        return True
    return False


def apply_guardrails(message: str, reply: str) -> str:
    if not (reply or "").strip():
        return reply
    if wants_code_fences(message):
        if _has_unbalanced_fences(reply):
            stripped = _strip_fences(reply)
            return _ensure_code_fences(stripped)
        return _ensure_code_fences(reply)
    return reply


_CODE_STARTS = (
    "def ",
    "class ",
    "async def ",
    "func ",
    "package ",
    "import ",
    "from ",
    "const ",
    "let ",
    "var ",
    "public ",
    "private ",
    "#include",
    "using ",
    "select ",
    "insert ",
    "update ",
    "delete ",
)


def _looks_like_key_value(line: str) -> bool:
    return bool(re.match(r"^\s*[A-Za-z0-9_.-]+\s*:\s*.+$", line))


def _looks_like_code_line(line: str, prev_is_code: bool) -> bool:
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return prev_is_code
    lower = stripped.lstrip().lower()
    if lower.startswith(_CODE_STARTS):
        return True
    if stripped.lstrip().startswith(("#!/bin/", "apiVersion:", "kind:", "metadata:", "spec:")):
        return True
    if _looks_like_key_value(stripped) and not stripped.lstrip().startswith(("-", "*")):
        return True
    if re.match(r"^\s{4,}\S", stripped):
        return True
    if any(sym in stripped for sym in ("{", "}", ";", "=>", "==", "!=", "::", "<-", "->")):
        return True
    if re.match(r"^\s*[-+*/]=?\\s*\\w+", stripped):
        return True
    return False


def _guess_language(block_lines: list[str]) -> str:
    first = ""
    for ln in block_lines:
        if ln.strip():
            first = ln.strip()
            break
    if not first:
        return "text"
    lower = first.lower()
    if lower.startswith("package ") or lower.startswith("func "):
        return "go"
    if lower.startswith("def ") or lower.startswith("async def ") or lower.startswith("import ") or lower.startswith("from "):
        return "python"
    if lower.startswith("#!/bin/bash") or lower.startswith("#!/usr/bin/env bash"):
        return "bash"
    if first.startswith("{") or first.startswith("["):
        return "json"
    if ":" in first and not any(sym in first for sym in (";", "{", "}")):
        return "yaml"
    if lower.startswith("class ") and "{" in first:
        return "ts"
    if lower.startswith("select ") or lower.startswith("insert ") or lower.startswith("update ") or lower.startswith("delete "):
        return "sql"
    return "text"


def _ensure_code_fences(reply: str) -> str:
    lines = reply.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    min_lines = 4
    in_fence = False
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        if not _looks_like_code_line(line, False):
            out.append(line)
            i += 1
            continue
        # expand possible code block
        start = i
        j = i
        code_line_count = 0
        prev_is_code = False
        while j < n:
            ln = lines[j]
            is_code = _looks_like_code_line(ln, prev_is_code)
            if not is_code and ln.strip():
                break
            if is_code and ln.strip():
                code_line_count += 1
            prev_is_code = is_code
            j += 1
        block = lines[start:j]
        if len(block) >= min_lines and code_line_count >= 3:
            lang = _guess_language(block)
            out.append(f"```{lang}".rstrip())
            out.extend(block)
            out.append("```")
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out).rstrip()


def _has_unbalanced_fences(text: str) -> bool:
    count = 0
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            count += 1
    return (count % 2) == 1


def _strip_fences(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).rstrip()
