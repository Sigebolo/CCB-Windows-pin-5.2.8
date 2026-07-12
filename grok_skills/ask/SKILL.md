---
name: ask
description: Async via ask, end turn immediately; use when user explicitly delegates to any AI provider (gemini/codex/opencode/droid/mimo/claude); NOT for questions about the providers themselves.
metadata:
  short-description: Ask AI provider asynchronously
---

# Ask AI Provider (Async)

Send the user's request to specified AI provider asynchronously.

## Usage

The first argument must be the provider name, followed by the message:
- `gemini` - Send to Gemini
- `codex` - Send to Codex
- `opencode` - Send to OpenCode
- `claude` - Send to Claude
- `mimo` - Send to MiMo
- `kiro` - Send to Kiro
- `droid` - Send to Droid

## Execution (MANDATORY)

**Do NOT use `run_in_background: true`** — it breaks stdin piping and the message is silently lost.

```
$env:CCB_CALLER='grok'; "$MESSAGE" | ask $PROVIDER
```

For multi-line messages:

```
$MESSAGE = @"
Your multi-line message here
"@
$env:CCB_CALLER='grok'; $MESSAGE | ask $PROVIDER
```

## Rules

- Follow the **Async Guardrail** rule in project instructions (mandatory).
- Local fallback: if output contains `CCB_ASYNC_SUBMITTED`, end your turn immediately.
- If submit fails (non-zero exit):
  - Reply with exactly one line: `[Provider] submit failed: <short error>`
  - End your turn immediately.

## Anti-Patterns (DO NOT)

- **NEVER use `run_in_background: true`** for ask commands — stdin is not delivered to the background process
- **NEVER use `Start-Process`** — same stdin issue

## Message Quality Rules (MANDATORY)

When composing the message to send:
1. **Complete message only** — never send partial, truncated, or incomplete requests
2. **Include enough context** — provide background so the other provider can respond effectively without needing to ask clarifying questions
3. **State your reasoning** — briefly explain WHY you're delegating, not just WHAT you want done
4. **One request at a time** — do not send follow-up messages until the previous request is fully processed

## Examples

- `/ask codex What is 12+12?`
- `/ask claude Refactor this function for better readability`
- `/ask gemini Brainstorm UI layout options`
