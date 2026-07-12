---
name: ask
description: Send via ask, end turn immediately; use when user explicitly delegates to any AI provider; NOT for questions about the providers themselves.
metadata:
  short-description: Ask AI provider asynchronously
---

# Ask AI Provider

Send the user's request to the specified AI provider via ask.

## Usage

The first argument must be the provider name, followed by the message:
- `gemini` - Send to Gemini
- `codex` - Send to Codex
- `opencode` - Send to OpenCode
- `claude` - Send to Claude
- `mimo` - Send to MiMo

## Execution (MANDATORY)

**Do NOT use `run_in_background: true`** — it breaks stdin piping.

### Windows (PowerShell) — use direct python call

```powershell
$MESSAGE = @"
Your message here
"@
$env:CCB_CALLER='kiro'; $MESSAGE | python "C:\Users\Administrator\AppData\Local\codex-dual\bin\ask" $PROVIDER --foreground --timeout 120
```

For simple single-line messages:

```powershell
$env:CCB_CALLER='kiro'; "Your message" | python "C:\Users\Administrator\AppData\Local\codex-dual\bin\ask" $PROVIDER --foreground --timeout 120
```

### Linux/Mac — use CCB_CALLER wrapper

```bash
CCB_CALLER=kiro ask $PROVIDER "$MESSAGE"
```

## Rules

- After running the command, say "[Provider] processing..." and immediately end your turn.
- Do not wait for results or check status in the same turn.
- The task ID and log file path will be displayed for tracking.

## Message Quality Rules (MANDATORY)

When composing the message to send:
1. **Complete message only** — never send partial, truncated, or incomplete requests
2. **Include enough context** — provide background so the other provider can respond effectively
3. **State your reasoning** — briefly explain WHY you're delegating, not just WHAT you want done
4. **One request at a time** — do not send follow-up messages until the previous request is fully processed

## Examples

- `/ask opencode What is 12+12?`
- `/ask claude Refactor this function for better readability`
- `/ask mimo Analyze this bug and suggest a fix`

## Notes

- If it fails, check backend health with the corresponding ping command.
- On Windows, always use the PowerShell syntax with `$env:CCB_CALLER='kiro'`, never bash syntax.
