---
name: ccb-bootstrap
description: Prepare any project folder for the default CCB team (grok,claude,mimo,opencode). Use when starting work in a new directory / project battlefield.
---

# CCB Bootstrap (new project)

```powershell
cd D:\path\to\project
ccb-bootstrap
# or force rewrite:
ccb-bootstrap --force
# fallback:
python "$env:LOCALAPPDATA\codex-dual\bin\ccb-bootstrap"
```

Then start panes:

```powershell
ccb
# or
ccb -a
```

Default squad: **grok** (leader) → claude → mimo → opencode.
See `docs/WINDOWS.md` on the Windows pin repo.
