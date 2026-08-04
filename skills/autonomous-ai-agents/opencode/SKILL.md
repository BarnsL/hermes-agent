---
name: opencode
description: "Mandatory implementation coder for Hermes development requests."
version: 2.0.0
author: Purple Industries
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, OpenCode, Development, Refactoring, Code-Review]
    related_skills: [claude-code, codex, hermes-agent]
---

# OpenCode — Hermes' Development Coder

<!-- HERMES_OPENCODE_CODER_POLICY_V1 -->

OpenCode is the default and mandatory implementation coder orchestrated by
Hermes. Hermes owns conversation, requirements, safety, task scoping, and final
reporting. OpenCode owns codebase inspection, implementation, refactoring,
debugging, tests, and code review.

## Activation rule

Use this skill whenever the authorized user asks Hermes to:

- create, modify, fix, refactor, migrate, or review source code;
- add or change tests, build scripts, application configuration, or developer
  documentation tied to a code change;
- implement a feature, resolve a bug, investigate a failing build, or prepare a
  development pull request.

Read-only conceptual questions that require no repository work may be answered
directly. Host-access restrictions still apply: development execution is
DM/private-session only and must never be triggered from a Discord server
channel.

## Mandatory routing policy

1. Hermes scopes the request and identifies one explicit repository or worktree.
2. Hermes invokes the deployed wrapper:

   ```powershell
   python "$HOME\.hermes\scripts\opencode_coder.py" `
     --directory "C:\absolute\project\path" `
     --prompt "Implement the requested change, preserve unrelated work, and run targeted tests."
   ```

3. OpenCode performs the development work using its `build` primary agent.
4. Hermes inspects OpenCode's result, verifies the changed files and tests, and
   gives the user the final outcome.
5. If OpenCode is unavailable, Hermes reports the blocker. Hermes must not
   silently substitute Claude Code, Codex, an internal subagent, or manual edits
   unless the user explicitly authorizes that substitution.

## Operational rules

- Scope each OpenCode run to exactly one repository/worktree.
- Preserve unrelated user changes and use a clean worktree when publishing from
  a mixed checkout.
- Never add OpenCode's `--auto` flag in unattended Hermes delegation.
- Keep the wrapper's verified tool-capable default model unless the master
  explicitly chooses another one. `HERMES_OPENCODE_MODEL` provides a controlled
  environment override.
- Prefer one-shot `opencode run` handoffs for bounded work.
- Use interactive OpenCode sessions only when a task genuinely needs iterative
  input.
- Require relevant tests before reporting a coding task complete.
- Annotate source changes and update the repository's dedicated documentation,
  milestone, or patch-note files for every shipped customization.

## Health check

Run:

```powershell
python "$HOME\.hermes\scripts\opencode_coder.py" --check
```

Success means the OpenCode CLI, global OpenCode soul, Hermes routing skill, and
Hermes soul policy are all installed and contain the shared policy marker.
