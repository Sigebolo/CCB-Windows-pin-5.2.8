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

## Start CCB in any project folder

Team work is **per directory**. For a new codebase:

```powershell
cd D:\path\to\your\project
ccb-bootstrap          # writes .ccb/ccb.config = grok,claude,mimo,opencode
ccb                    # or: ccb -a
```

- First provider in config = **team leader** (current pane) — default `grok`
- Run from a **clean WezTerm tab** (not nested inside an existing agent if possible)
- Session files live under that project's `.ccb/` (gitignored)

## Smoke checks

```powershell
ccb -v
ccb-ping grok
ccb-ping claude
ccb-ping mimo
ccb-ping opencode
```

If MiMo inbound fails, ensure a live MiMo pane exists (`MiMoCode` / `CCB-Mimo` title). Pane-poll ignores the injected instruction text when detecting `CCB_DONE`.
