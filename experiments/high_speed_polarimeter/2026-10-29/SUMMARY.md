# 2026-10-29 (UTC; evening of 2026-10-28 local) — EOM swap to Thorlabs EO-PM-NR-C1, first attempts

Converted from the day's PICKUP note at the 2026-11-04 close-out. All runs kept in `runs/` (state A/B evidence and the second-driver clamp exist nowhere else). **Vπ was not measured this day;** see `../2026-11-04/SUMMARY.md` for the first valid Vπ of this EOM.

**Where things stood.** EOM swapped Newport 4004 → Thorlabs EO-PM-NR-C1. Old chain (preamp → 3211): the ±0.48 V tee window showed no fringe peak. Notebook Vπ (0.73 V) and drift results from these runs are invalid by method; the step/sweep/network bias (−0.2061 V) came from 2 noise samples.
Inferred only (unapproved fit/ratio): retardance Vπ ≈ 0.81–0.85 V tee (≈ 325–340 V via the unverified 406 V/V) vs ≈ 260 V expected. Two optical states: A (trough ≈ dark) / B (trough +8 → +19 mV, noisier); all step/sweep/network runs are state B.
Second, black-box driver (`runs/011006_agent-vpi-steps`): negative inputs looked clamped; no peak/trough up to +0.97 V; gain unknown.

**Runs:** `003605_dc-points`, `003623_sine`, `003714_square`, `003721_sweep`, `003943_network`, `004033_sine`, `004359_network`, `004540_sine`, `005039_sine` (examined by eye only), `005330_sine` (not examined), `011006_agent-vpi-steps` (agent script text in its metadata).

**Open items carried forward** (status 2026-11-04): phase vs retardance Vπ — resolved (retardance); reaching a peak — resolved by Sénarmont bias; label/redo state-B runs — open; DMM HV per command — open; dark at 0 dB — measured 2026-11-04 (−6.2 mV); voltage limit / datasheet — open; mode-hop hypothesis — untested.
