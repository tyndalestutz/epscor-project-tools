# September 21: IN2 visibility audit

The original ~48.6% estimate included a large noise envelope. The replacement
passive routine measured **21.23% apparent contrast** across three captures,
using declared 0.5 Hz timing and an assumed zero dark voltage. An actual
blocked-light voltage was not measured. A later read-only audit found OUT2
configured with approximately 0.4 V offset, 0.4 V amplitude, and 0.466 Hz frequency.
Outputs were left unchanged. If connected to actuator two, that motion is
included: this is not an established isolated-actuator-one optical visibility.

See the [full audit](215158_pd-visibility_pd-visibility/audit.md),
[trace comparison](215158_pd-visibility_pd-visibility/contrast-audit.png), and
[report](215158_pd-visibility_pd-visibility/report.pdf).
These are historical results and limitations, not a check of today's bench state.

| Folder | Status / evidence | Use |
| --- | --- | --- |
| `205238_pd-visibility` | Header-only CSV; no completed result | Original startup attempt |
| `205333_pd-visibility` | Six captures; metadata records phi2 drive settings; visibility summaries are NaN | Diagnose original method; not a valid visibility estimate |
| `205549_pd-visibility` | Six captures; 48.57% percentile estimate; raw extrema exceed 100% | Rejected original estimate; raw traces retained |
| `214651_pd-visibility_pd-visibility` | Failed: sandbox denied socket access | Development connection diagnostic |
| `214714_pd-visibility_pd-visibility` | Failed: existing monitor unavailable | Development connection diagnostic |
| `214942_pd-visibility_pd-visibility` | Failed: insufficient period coverage; one raw capture saved | Exposed ignored scope-decimation setup; no accepted contrast |
| `215158_pd-visibility_pd-visibility` | Completed: three traces, recipe/status/log/report and output-state audit | Reference for software behavior; apparent contrast with the limitations above |

All original files are retained. The original script was removed from the
supported package; the current test is `pd-visibility` in the
[suite catalog](../../../python/polarization_locking/docs/test-guide.md).
Reproduce the physical setup and verify active outputs, actual external frequency,
PD coupling/gain and dark level before repeating. Never copy the 21.23% result
as a calibrated reference without those checks.
