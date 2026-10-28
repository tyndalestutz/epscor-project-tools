---
name: builder
description: Switch back to the default builder role after tutor mode. Use when the user invokes /builder or asks to return to implementation.
---

**First action, every time this skill is invoked (including repeat invocations), before anything else:**
if `~/.claude/bin/set-role` exists, run `~/.claude/bin/set-role builder`
(status-line role indicator; skip silently otherwise).

Leave tutor mode. Follow the builder rules in `AGENTS.md` from now on.
