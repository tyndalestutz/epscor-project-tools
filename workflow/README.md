# Scientist + AI workflow

How Tyndale and AI agents (Claude Code, Codex) work together in this repo.
The goal is **fast AI-assisted work that ends with me understanding and owning
every number I report**. This file is the design; revise it as we learn.

Status: v0, drafted 2026-10-28. Not yet used on a real measurement.

## 1. The problem this fixes

Black-box measurement suites produced a lot of good data but little understanding:

- **Visibility tool.** It assumed zero light = 0 V. Neither I nor the agent
  caught it for a day. Nothing in the code was wrong; a physical assumption was
  never written down, so nobody could check it.
- **Cycling Vπ estimator** (EOM notebook). Asked for a Vπ number, the agent
  produced a reconstructed voltage axis, 64-bin averaging, peak finding,
  local parabolas, a self-referential sine check and five hand-picked cutoffs.
  Each step is defensible; together they are more than I can audit, and drive/
  response lag looks identical to hysteresis in that method.

Root causes:

1. Agents fill gaps in their knowledge of the bench with code instead of asking.
2. The old `AGENTS.md` rewarded defensive complexity ("never silently X",
   "always distinguish Y") and barely mentioned simplicity.
3. The agent that writes code is the worst reviewer of it.
4. My real validation (analog scope, imagined plot, function-generator
   reproduction) happened in my head and on the bench, never in the record.

## 2. Principles

1. **I own the science; agents own the plumbing.**
   - *Plumbing:* instrument drive, capture, limits, cleanup, saving raw data and
     provenance. Agents may work autonomously here; trust comes from tests and
     bench checks, not from me reading every line. Plumbing returns raw data
     and evidence only: no processing hides in it.
   - *Science:* what we measure, how a number comes out of raw data, what could
     fool us. Must stay small enough that I can explain it at a whiteboard.
2. **Simplest method first.** Start with something I could do with a ruler on
   the raw plot. Anything more needs a reason and my approval.
3. **Ask, don't infer.** If I can check a bench fact in under a minute
   ("does IN2 look like a clean sine on the scope?"), the agent asks.
4. **Predict before measuring.** I say what plot I expect. The agent predicts
   independently. Disagreement before data is the cheapest learning there is.
5. **Analog ground truth is evidence.** What I saw on the scope or produced
   with the function generator gets written down next to the data.
6. **I run the hardware.** Agents write cells and tools; I execute anything that
   drives or connects to an instrument unless I explicitly hand over a
   specific, bounded run.
7. **Short, ranked caveats.** Top few limitations, most important first. A
   caveat on every sentence hides the ones that matter.

## 3. Roles

| Role | Job | Where it runs | Spec |
| --- | --- | --- | --- |
| **Builder** | Implements exactly what the plan says, minimally. Proposes plan changes instead of slipping them into code. | Main session (default) | [`AGENTS.md`](../AGENTS.md) "Builder rules" |
| **Tutor** | Gets me to real understanding: predictions, derivations, explain-back. Not a yes-man. | Mode in the main conversation (`/tutor` in Claude, `$tutor` in Codex) | [`roles/tutor.md`](roles/tutor.md) |
| **Adversary** | Fresh-context reviewer. Compares work against the agreed plan and reports drift, unapproved processing, physics traps and excess complexity **directly to me**. | Subagent (no shared context with the builder) | [`roles/adversary.md`](roles/adversary.md) |

When to call the adversary:

- after a measurement plan is drafted (physics traps),
- after the builder finishes a chunk (drift, sneaked-in processing),
- before I trust a number.

Cross-model review (e.g. Codex builds, Claude reviews, or the reverse) is
encouraged: different models are less likely to share blind spots.

## 4. The measurement plan is the contract

The adversary can only say "this doesn't match what we agreed" if the agreement
is written. Every measurement, big or small, has a plan with these parts:

| Part | Who leads | Content |
| --- | --- | --- |
| Question | Me | One sentence. |
| Prediction | Me and agent, separately | Expected plot shape and rough numbers. |
| Derivation | Agent drafts, I own | The math from raw signal to result, hand-derivable; sympy check where useful. |
| What could fool us | Adversary, then me | Traps (offsets, drift, lag, stale reads, saturation, gain changes). I decide which get measured. |
| Analog check | Me | What the scope/function generator showed, typed by hand. |
| Acquire | Tested tool | One or two lines. No safety code visible. |
| Raw look | Always first | Raw trace before any processing. |
| Simplest estimate | I approve the method | Ruler-on-plot level. |
| Compare | Together | Against prediction and analog check. Surprises become the next question. |
| **Decisions** | I approve each row | Every threshold, fit, filter, bin, average, inferred axis or exclusion, with one line of why. **Starts empty.** |

Decisions table format:

| # | Decision | Why | Approved |
| --- | --- | --- | --- |
| 1 | Subtract dark level measured with beam blocked | PD offset is not 0 V | TS 2026-10-29 |

Anything in the analysis code that is not in the plan or the Decisions table is
a finding for the adversary.

**Minimum plan for a small number:** Question, Prediction, Decisions. Write
these in a markdown cell (or the tool's docstring) *before* analysis code
exists.

## 5. Big experiments vs small numbers

- **Big experiments → JupyterLab notebooks.** The table of contents is the
  roadmap. Each section follows the plan structure above. Code cells stay short
  and call tested modules; derivations and descriptions live in markdown.
- **Small "get me a number" → one-off repeatable tools.** Every such tool:
  saves the raw plot, prints its method in one plain paragraph, records the
  run, and has a known-answer test. Prefer a **physical** known-answer test
  where possible: feed a known function-generator signal (or a known dark/DC
  level) and check the tool recovers it.

## 6. Working with an agent beside JupyterLab

v0 protocol (terminal agent + JupyterLab in the browser):

1. Save the notebook in JupyterLab.
2. Ask the agent for the cell/edit. It edits the `.ipynb` on disk.
3. In JupyterLab: **File → Reload Notebook from Disk**. Do not let an older
   open copy autosave over the agent's edit.
4. Read the new cell, then run it yourself.

Agents never execute notebook cells that connect to or drive hardware.

Later experiment (not v0): live notebook control (Jupyter MCP server or the
Jupyter AI extension) so the agent edits the open notebook directly. Evaluate
on a non-hardware notebook first.

## 7. Where the files live

Role text lives once, in `workflow/roles/`. Tool-specific files are thin
pointers to it, so Claude and Codex follow the same rules.

| Purpose | Shared source | Claude Code | Codex |
| --- | --- | --- | --- |
| Project rules (builder) | `AGENTS.md` | reads `AGENTS.md` natively (no `CLAUDE.md`) | `AGENTS.md` |
| Adversary | `workflow/roles/adversary.md` | `.claude/agents/adversary.md` | `.codex/agents/adversary.toml` |
| Tutor | `workflow/roles/tutor.md` | `.claude/skills/tutor/SKILL.md` | `.agents/skills/tutor/SKILL.md` |
| Enforcement | (future) shared scripts | `.claude/settings.json` hooks | `.codex/hooks.json` |

Notes:

- Do **not** add a `CLAUDE.md`: if one exists, Claude Code reads it instead of
  `AGENTS.md`.
- New `.claude/agents/` or skill folders need a session restart to be seen.
- Codex project hooks run only after the project is trusted and the hook is
  approved with `/hooks`.

## 8. Status and next steps

v0 (this increment): this design, role specs, compacted `AGENTS.md`, thin
Claude/Codex adapters.

Next:

1. **Template notebook** (step 2): one section of the plan structure with short
   prompts and an empty Decisions table.
2. **Retrofit the EOM notebook** (step 3): keep device setup; replace the
   measurement sections with fresh, simple plans designed together.
3. Deferred until v0 has been used on real measurements:
   - hooks (enforcement scripts for both tools),
   - live JupyterLab integration,
   - personal cross-repo files (`~/.claude/`, `~/.codex/AGENTS.md`),
   - extracting this folder into a reusable kit for other repos.

Why no hooks in v0: the rules that matter most here (plan drift, unapproved
processing) need judgment, which is the adversary's job. Hooks are for
mechanical rules; add them when a mechanical rule keeps being broken.

## 9. Change log

- 2026-10-28: v0 drafted (Claude Code session).
