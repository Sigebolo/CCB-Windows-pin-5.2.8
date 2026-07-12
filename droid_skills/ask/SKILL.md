---
name: ask
description: Send via ask, end turn immediately; use when user explicitly delegates to any AI provider (gemini/codex/opencode/droid/mimo); NOT for questions about the providers themselves.
metadata:
  short-description: Ask AI provider asynchronously
---

# Ask AI Provider

Send the user's request to the specified AI provider via ask.

## Usage

The first argument must be the provider name. The message MUST be provided via stdin
(heredoc or pipe), not as CLI arguments, to avoid shell globbing issues:
- `gemini` - Send to Gemini
- `codex` - Send to Codex
- `opencode` - Send to OpenCode
- `claude` - Send to Claude
- `mimo` - Send to MiMo
- `kiro` - Send to Kiro
Optional flags after the provider:
- `--foreground` / `--background`
- Env overrides: `CCB_ASK_FOREGROUND=1` / `CCB_ASK_BACKGROUND=1`

## Execution (MANDATORY)

```bash
CCB_CALLER=droid ask "$PROVIDER" <<'EOF'
$MESSAGE
EOF
```

## Rules

- STRICT: Execute the bash snippet in the Execution section, then immediately end your turn.
- Do not run any other commands/tools besides this snippet (no `gask/cask/oask/lask/dask`, no `pend`, no `ping`, no retries) unless the user explicitly asks.
- Do not add any extra commentary/output (including "processing..."); the `ask` command already prints the task id and log path.
- Do not wait for results or check status in the same turn.

## Message Quality Rules (MANDATORY)

When composing the message to send:
1. **Complete message only** — never send partial, truncated, or incomplete requests
2. **Include enough context** — provide background so the other provider can respond effectively
3. **State your reasoning** — briefly explain WHY you're delegating, not just WHAT you want done
4. **One request at a time** — do not send follow-up messages until the previous request is fully processed

## Examples

- `/ask gemini What is 12+12?` (send via heredoc)
- `CCB_CALLER=droid ask opencode <<'EOF'`
  `Refactor this function for better readability`
  `EOF`

## Notes

- If it fails, stop after reporting the failure output; only run diagnostics in a new turn if the user requests it.
