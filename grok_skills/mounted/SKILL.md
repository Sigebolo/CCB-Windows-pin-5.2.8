---
name: mounted
description: List which CCB providers are mounted for the current project (session + online). Outputs JSON when available.
---

# CCB Mounted

```powershell
ccb-mounted
# or ping each:
foreach ($p in "claude","mimo","opencode","grok") { ccb-ping $p }
```
