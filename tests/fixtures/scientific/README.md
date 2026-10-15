# Qualified synthetic contracts

`contracts.json` is hand-authored synthetic evidence, qualified against integrated
source revision `42e8d61c7d5d4b8198510cc34eb3df5fb33187b6`. No archive was copied,
no bench was accessed and no actual instrument identity or network setting is
included. Fixture version 1 is a test-data version, not an application schema.
See the [coverage inventory](../../../docs/engineering/scientific-regression.md).

## Jones reference states

Inputs are complex Jones components `[real, imaginary]`, in arbitrary common field
amplitude units. With `z = conjugate(Ex) * Ey`, independent algebra gives:

- Q0 = |Ex|² + |Ey|² (arbitrary power units).
- Q1 = |Ey|² - |Ex|², Q2 = 2 Re(z), Q3 = -2 Im(z).
- Normalized direction is (Q1, Q2, Q3) / Q0, dimensionless.

These formulas produce the stored integer references directly, independently of
production matrices. They protect the repository's sign and axis convention,
including unequal-amplitude elliptical polarization. No right/left handedness
claim is made without an observer/time convention. States represent mathematical
inputs, not successful or failed acquisitions. Multiplication by -2 must multiply
power by four without changing direction; multiplication by i tests global phase
invariance. Circular azimuth remains undefined and eta is -pi/4 radians for the
positive-imaginary-y reference. The dark-boundary test uses exact binary amplitudes
and an explicitly chosen .25 power threshold, without changing the default.

These fixtures preserve the current convention as an explicit compatibility
baseline, not a requirement that every future tool use it internally. Keep Stokes
axes/signs, angle definitions, reference axes and units documented and consistent
at shared interfaces. A tool using another convention must identify it and use an
explicit, documented conversion at the boundary. A deliberate project convention
change requires reviewed conversion/compatibility behavior and updated independent
references; do not silently relabel coordinates or regenerate expectations merely
to make a changed result pass. Configurable conventions are not implemented by
this PR.

The mathematical dark threshold above masks undefined polarization in modeled
field units. It is not a photodetector dark measurement, electronic offset, noise
floor or calibrated optical-power threshold. Zero recorded volts must not be
assumed to establish any of those quantities.

Float64 arithmetic on components of magnitude at most four allows absolute
1e-13 for raw products/power, 1e-14 for normalized direction/angle, with rtol=0.
These are roundoff allowances for analytic identities, not experimental error
bars. Discrete masks, input retention and zero-field rejection are exact.

## High-speed mock acquisition

The config requests two points, one PAX sample and one full PD buffer per point
at existing resolved mock defaults. Event sequences specify baseline before
commands and command/acknowledgment/settle/capture/PAX/completion ordering. The
synthetic signed PD period is in input volts; 2048 repeats form a complete 16384
sample float64 buffer. Exact comparisons protect negative and zero samples,
shape/dtype, byte references and absence of trailing/unreferenced buffers.
No optical transfer model is encoded by this mock.

Raw detector voltage, dark-subtracted voltage and calibrated optical power must
remain distinguishable. Future dark correction must reference a measured baseline
and its acquisition conditions; power conversion must identify the applicable
instrument calibration and units. Preserve raw samples and make any subtraction
or conversion traceable. These fixtures test raw signed-buffer retention only;
they do not implement or validate dark subtraction or voltage-to-power calibration.

Three cases represent completed acquisition, interruption during the first
point's PAX read, and read failure at that position. Both failure cases also raise
on disconnect *after mock zeroing*. Tests distinguish execution status from cleanup
failure, retain the completed PD buffer and error event, check RUNNING state and
source provenance before connect, and verify offline quality/report replay cannot
rewrite raw inputs. Complete mock runs remain scientifically UNKNOWN; incomplete
acquisition is FAIL. All statuses, units/schema strings and call/event ordering
are exact contracts. Generated timestamps and PDF bytes are not goldens.

No synthetic output establishes delivered voltage, physical cleanup, PAX freshness,
synchronization, calibration or optical performance. Zeroing before the injected
cleanup exception is deliberately limited to the mock; real failed cleanup must
be treated as an unknown physical state.

## Run only these contracts

From the repository root, with the documented offline environment installed:

```bash
scratch=$(mktemp -d /tmp/epscor-fixtures.XXXXXXXX)
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts/offline:python
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg
export MPLCONFIGDIR="$scratch/mpl" TMPDIR="$scratch"
python -m pytest -p no:cacheprovider python/high_speed_polarimeter/tests/test_scientific_fixtures.py --basetemp="$scratch/high"
python -m pytest -p no:cacheprovider python/field_propogation/tests/test_scientific_fixtures.py --basetemp="$scratch/field"
rm -r -- "$scratch"
```

Use separate pytest processes, as in the unified runner, because application test
modules can share filenames. New contracts run automatically in their existing
suite directories; no CI or runner changes are needed.
