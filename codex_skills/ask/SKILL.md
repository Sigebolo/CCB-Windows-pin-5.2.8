---
name: ask
description: Send the user's request to specified AI provider asynchronously
metadata:
  short-description: Ask AI provider asynchronously
---

# Ask AI Provider

Send the user's request to the specified AI provider via ask.

## Usage

The first argument must be the provider name. The message MUST be provided via stdin
(heredoc or pipe), not as CLI arguments, to avoid shell globbing issues:
- `gemini` - Send to Gemini
- `claude` - Send to Claude
- `opencode` - Send to OpenCode
- `mimo` - Send to MiMo
- `kiro` - Send to Kiro
- `grok` - Send to Grok (xAI)

## Execution (MANDATORY)

```bash
CCB_CALLER=codex ask $PROVIDER <<'EOF'
$MESSAGE
EOF
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

- `/ask gemini What is 12+12?` (send via heredoc)
- `CCB_CALLER=codex ask opencode <<'EOF'`
  `Refactor this function for better readability`
  `EOF`

## Notes

- If it fails, check backend health with the corresponding ping command (`ccb-ping <provider>` (e.g., `ccb-ping gemini`)).
- Codex-managed sessions default to foreground; use `--background` or `CCB_ASK_BACKGROUND=1` for async.
