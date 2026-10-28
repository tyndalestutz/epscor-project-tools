---
name: archivist
description: Close out the lab day with the archivist. Use when the user invokes /archivist [quick|standard|full] or asks to close out, wrap up, or leave a pickup note.
---

**First action, every time this skill is invoked (including repeat invocations), before anything else:**
if `~/.claude/bin/set-role` exists, run `~/.claude/bin/set-role archivist`
(status-line role indicator; skip silently otherwise).

Tier = the argument (`quick`, `standard` or `full`; ask if missing). Spec: `workflow/roles/archivist.md`.

1. **Brief.** Write a short brief of what exists only in this conversation: approvals and pending
   decisions, bench observations and wiring changes, what confused the scientist, what is unfinished.
2. **Archivist.** Start the `archivist` subagent with the tier, the brief and pointers to today's run
   folders, notebook and notes. It proposes; it does not edit.
3. **Scientist.** Relay its report concisely: gaps, settings list (the scientist marks what they don't
   fully understand; add those to the tutor queue), and the numbered plan with irreversible steps marked.
   Wait for approval of each irreversible step.
4. **Builder.** Carry out only the approved plan (`quick`: write PICKUP.md, nothing else).
5. **Adversary.** For `standard` and `full`, have the adversary check the result against the approved
   plan and the disk; report its findings.
6. Finish with `~/.claude/bin/set-role builder`.
