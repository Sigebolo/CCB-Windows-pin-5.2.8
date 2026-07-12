---
name: ask
description: >
  Send CCB SMS to teammate AI providers (claude/mimo/opencode/codex/gemini/droid/kiro).
  Use when delegating work, asking review, cross-agent debug, or team coordination.
  Grok is team leader (CCB_CALLER=grok). Works in any project folder after ccb-bootstrap + ccb.
metadata:
  short-description: CCB ask teammates (Grok leader)
---

# CCB Ask (Grok = team leader)

Send a complete message to another AI provider via CCB. Prefer this over inventing your own IPC.

## Default team (Windows pin)

| Role | Provider |
|------|----------|
| **leader / designer** | `grok` (you) |
| executor | `claude` |
| inspiration | `mimo` |
| reviewer | `opencode` |

Config: `.ccb/ccb.config` → `grok,claude,mimo,opencode` (first = leader pane).

## Providers

- `claude` - implementation / primary coding
- `mimo` - brainstorm / alternate view
- `opencode` - review / second opinion
- `codex` / `gemini` / `droid` / `kiro` - if mounted

## Execution (MANDATORY)

**Do NOT use `run_in_background: true`** — stdin is lost and the message never arrives.

### Async (default for long tasks)

```powershell
$env:CCB_CALLER = "grok"
$MESSAGE = @"
Complete request with context and why you are asking.
"@
$MESSAGE | ask $PROVIDER
```

If output contains `[CCB_ASYNC_SUBMITTED`:
1. Reply one line: `<Provider> processing...`
2. **END YOUR TURN IMMEDIATELY**
3. Do not poll/sleep/`pend` in the same turn — wait for completion hook or later user turn

### Foreground (smoke test / need reply now)

```powershell
$env:CCB_CALLER = "grok"
"Reply with exactly: TOKEN_OK" | ask claude --foreground --timeout 180
```

### PATH fallback (if `ask` not found)

```powershell
$env:CCB_CALLER = "grok"
$ASK = Join-Path $env:LOCALAPPDATA "codex-dual\bin\ask"
"msg" | python $ASK claude --foreground --timeout 180
```

## Message quality

1. Complete message only — never partial
2. Enough context for the other agent to act without asking you
3. State WHY you are delegating
4. One request at a time per provider

## Related commands

```powershell
ccb-ping claude          # health
ccb-ping mimo
ccb-ping opencode
pend claude              # latest reply
ccb-bootstrap            # new project folder: write .ccb/ccb.config
ccb                      # start team panes in current directory
```

## New project folder workflow

```powershell
cd D:\path\to\project
ccb-bootstrap
ccb -a
# then use this skill to SMS teammates
```

## Anti-patterns

- `run_in_background: true` / `Start-Process` for ask
- Flooding multiple concurrent asks to the same provider
- Forgetting `CCB_CALLER=grok` (caller is required by askd)
- Assuming GitHub latest CCB — this team uses **Windows pin 5.2.8**
