---
name: cping
description: Test CCB provider connectivity (claude/mimo/opencode/grok/...). Use before blaming agents when SMS fails.
---

# CCB Ping

```powershell
ccb-ping <provider>
# fallback:
python "$env:LOCALAPPDATA\codex-dual\bin\ccb-ping" <provider>
```

Providers: `grok`, `claude`, `mimo`, `opencode`, `codex`, `gemini`, `droid`, `kiro`

Healthy examples: `Session OK`, `Grok OK pane=...`, `MiMo OK pane=...`, `OpenCode connection OK`.
