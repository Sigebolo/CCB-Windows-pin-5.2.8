---
name: pend
description: View latest reply from a CCB provider (claude/mimo/opencode/...). Use after async ask completes or to audit last SMS.
---

# CCB Pend

```powershell
pend <provider> [N]
# fallback:
python "$env:LOCALAPPDATA\codex-dual\bin\pend" <provider> 1
```

Shows the latest N conversation pairs for that provider.
