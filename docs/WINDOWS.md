# Windows pin (do not follow GitHub HEAD blindly)

This machine / team runs a **Windows-compatible pin** of CCB, not necessarily the latest release on GitHub.

| Item | Value |
|------|--------|
| **Pinned version** | `5.2.8` |
| **Source tree** | local clone (this repo) |
| **Intended remote** | `https://github.com/Sigebolo/ACP_Bridge.git` (team Windows pin; wipe old ACP content first) |
| **Runtime install** | `%LOCALAPPDATA%\codex-dual\` |
| **Launcher** | `%LOCALAPPDATA%\bin\ccb.cmd` → `python …\codex-dual\ccb` |
| **Terminal** | Native **WezTerm** (not WSL WezTerm) |

## Why pin?

Newer upstream builds have broken or incomplete **native Windows** support (WezTerm CLI, PowerShell wrappers, path handling, process detach). This pin is known to work on Windows with the multi-pane workflow.

## Rules

1. **Do not** run `ccb update` / pull GitHub `main` just to “get latest” unless someone has verified Windows.
2. Prefer **cherry-pick** single fixes from upstream into this pin.
3. After editing the source tree, **sync to the install dir** used by `ccb.cmd`:
   ```powershell
   # Example: copy what you changed into the runtime install
   $src = "D:\mimo code\claude_codex_bridge"
   $dst = "$env:LOCALAPPDATA\codex-dual"
   Copy-Item -Force "$src\ccb" "$dst\ccb"
   # plus any changed lib/bin files you rely on
   ```
4. Keep CCB in the **same environment** as the agent CLIs (native Windows together, or WSL together — do not mix).

## Default squad (this pin)

Configured team (first = leader / current pane):

```text
grok,claude,mimo,opencode
```

- Project: `.ccb/ccb.config`
- Global fallback: `%USERPROFILE%\.ccb\ccb.config`

## Smoke checks

```powershell
ccb -v
ccb-ping grok
ccb-ping claude
ccb-ping mimo
ccb-ping opencode
```

If MiMo inbound fails, ensure a live pane titled `CCB-Mimo` exists (`ccb` four-pane layout), not only the inbox under `~\.mimocode\inbox`.
