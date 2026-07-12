<!-- CCB_CONFIG_START -->
## AI Collaboration
Use `/ask <provider>` to consult other AI assistants (claude/mimo/opencode/grok).
Use `/cping <provider>` to check connectivity.
Use `/pend <provider>` to view latest replies.

Providers: `claude`, `mimo`, `opencode`, `grok` (and optionally `codex`, `gemini`, `droid`, `kiro` if installed)

## Cross-Provider Communication Rules (MANDATORY)

When sending messages to other providers via `/ask`:
1. **Complete messages only** — never send partial, truncated, or incomplete requests
2. **Include context** — provide enough background so the other provider can respond effectively
3. **State your reasoning** — when delegating work, briefly explain WHY you're asking, not just WHAT
4. **Wait for completion** — do NOT send follow-up messages until the previous request is fully processed
5. **One request at a time** — do not flood another provider with multiple concurrent requests

When responding to requests from other providers:
1. **Read the full request** before starting your response
2. **Include your decision-making process** — explain what you considered, what trade-offs you evaluated, and why you chose your approach
3. **Provide complete, actionable output** — do not truncate or leave partial results
4. **End with the required marker** — always finish with the `CCB_DONE:` line as instructed
5. **Do not echo back the request** — focus on the answer/solution, not repeating the question

## Async Guardrail (MANDATORY)

When you run `ask` (via `/ask` skill OR direct `Bash(ask ...)`) and the output contains `[CCB_ASYNC_SUBMITTED`:
1. Reply with exactly one line: `<Provider> processing...` (use actual provider name, e.g. `Codex processing...`)
2. **END YOUR TURN IMMEDIATELY** — do not call any more tools
3. Do NOT poll, sleep, call `pend`, check logs, or add follow-up text
4. Wait for the user or completion hook to deliver results in a later turn

This rule applies unconditionally. Violating it causes duplicate requests and wasted resources.

## Critical: Ask Execution Rules (MANDATORY)

**All `ask` commands MUST run synchronously (no `run_in_background: true`).**

Background execution breaks stdin piping — the message is silently lost. This is the #1 cause of failed dispatches.

```
# CORRECT — synchronous, stdin works (Grok as team leader)
$env:CCB_CALLER='grok'; "$MESSAGE" | ask $PROVIDER

# WRONG — background breaks stdin
PowerShell(run_in_background: true, ...ask...)
Start-Process ask -ArgumentList $PROVIDER
```

<!-- CCB_ROLES_START -->
## Role Assignment

Abstract roles map to concrete AI providers. Skills reference roles, not providers directly.

| Role | Provider | Description |
|------|----------|-------------|
| `designer` / **team leader** | `grok` | Owns plans, coordination, and final decisions |
| `inspiration` | `mimo` | Creative brainstorming — reference only (never blindly follow) |
| `reviewer` | `opencode` | Scored quality gate — evaluates plans/code using Rubrics |
| `executor` | `claude` | Code implementation — writes and modifies code |

Default squad (ccb.config): `grok,claude,mimo,opencode` — **first = leader (current pane)**.

To change a role assignment, edit the Provider column above.
When a skill references a role (e.g. `reviewer`), resolve it to the provider listed here (e.g. `/ask opencode`).
<!-- CCB_ROLES_END -->

<!-- OPENCODE_REVIEW_START -->
## Peer Review Framework

The `designer` MUST send to `reviewer` (via `/ask`) at two checkpoints:
1. **Plan Review** — after finalizing a plan, BEFORE writing code. Tag: `[PLAN REVIEW REQUEST]`.
2. **Code Review** — after completing code changes, BEFORE reporting done. Tag: `[CODE REVIEW REQUEST]`.

Include the full plan or `git diff` between `--- PLAN START/END ---` or `--- CHANGES START/END ---` delimiters.
The `reviewer` scores using Rubrics defined in `AGENTS.md` and returns JSON.

**Pass criteria**: overall >= 7.0 AND no single dimension <= 3.
**On fail**: fix issues from response, re-submit (max 3 rounds). After 3 failures, present results to user.
**On pass**: display final scores as a summary table.
<!-- OPENCODE_REVIEW_END -->

<!-- MIMO_INSPIRATION_START -->
## Inspiration Consultation

For creative tasks (UI/UX design, copywriting, naming, brainstorming), the `designer` SHOULD consult `inspiration` (via `/ask`) for reference ideas.
The `inspiration` provider is often unreliable — never blindly follow. Exercise independent judgment and present suggestions to the user for decision.
<!-- MIMO_INSPIRATION_END -->

<!-- CCB_CONFIG_END -->
