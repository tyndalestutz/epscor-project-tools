# Optical flows and parameter files

[Back to simulations](../README.md)

An optical flow describes **what is connected to what**. A parameter file
supplies **the numerical values for a particular study**. Both are JSON with
`schema_version: 1`. Nothing in a configuration executes Python.

## A complete one-element example

Optical flow:

```json
{
  "schema_version": 1,
  "name": "My measured optic",
  "phases": [],
  "input": {"name": "Ein", "jones": ["ax", "ay"]},
  "elements": [
    {
      "name": "sample",
      "type": "diattenuator",
      "inputs": ["Ein"],
      "outputs": ["out"],
      "amplitude_x": "tx",
      "amplitude_y": "ty",
      "phase_x_rad": "phase_x",
      "phase_y_rad": "phase_y",
      "axis_rad": "axis"
    }
  ],
  "outputs": ["out"]
}
```

Parameter file:

```json
{
  "schema_version": 1,
  "name": "Measurement set 1",
  "description": "Replace these illustrative values with measurements.",
  "values": {
    "ax": 1,
    "ay": 1,
    "tx": 0.9,
    "ty": 0.7,
    "phase_x": 0,
    "phase_y": "pi/3",
    "axis": 0
  },
  "phase_values": {}
}
```

Run it with `run_simulation.py --flow my_flow.json --params my_params.json`.
File paths are interpreted relative to your current working directory. The
built-in default flow and parameters work from any working directory.

## Numerical values and units

- Amplitudes multiply the **field**. A power transmission of 0.81 means an
  amplitude of 0.9. Input power is `abs(Ex)**2 + abs(Ey)**2` in arbitrary units.
- All angles are radians. Numeric strings accept `pi`, `i`, parentheses, and
  `+`, `-`, `*`, `/`. For example, `"pi/4"`, `"0.8 + 0.2*i"`.
- Flow values can refer to names in `values`, for example `"loss_a"` or
  `"retardance_c/2"`. Parameter definitions themselves contain constants,
  not references to other parameters. Functions such as `sqrt` and `exp`
  are not accepted. Enter their numerical values instead.
- A complex input phase can be supplied directly as a complex amplitude, e.g.
  `["0.7071067811865476 + 0.7071067811865476*i", 1]`.
- Optical phases and instrument voltages are distinct. Convert voltage with a
  measured phase law before running this engine. The compatibility API retains
  `phase_from_rp_voltage`; it does not model hysteresis.

## Connections and elements

Each element has a unique `name`, a `type`, an `inputs` list, and an `outputs`
list. Define elements in optical order; forward references and cycles are
rejected. A field can feed only one element: create physical branches using a
splitter. List every terminal field in the flow's final `outputs`; intermediate
checkpoints are available through the Python API.

Names begin with a letter and contain only letters, digits, or underscores.
Optional `description` text can explain a flow, parameter set, or element.
Unknown keys and unknown parameter references produce an error rather than
silently reverting to defaults. Unused parameter values are reported.

| Type | Inputs → outputs | Settings |
| --- | --- | --- |
| `pbs` | 1 → 2 | `leakage_rad` (default 0). Lossless one-illuminated-input leakage model. |
| `npbs` | 2 → 2 | `mixing_rad`; optional `mixing_y_rad` for independent y splitting. |
| `phase` | 1 → 1 | `phase` names a control; optional `offset_rad` (default 0). |
| `attenuator` | 1 → 1 | `amplitude`, a nonnegative scalar field transmission. |
| `retarder` | 1 → 1 | `axis_rad`, `retardance_rad`. |
| `diattenuator` | 1 → 1 | `amplitude_x`, `amplitude_y`; optional `phase_x_rad`, `phase_y_rad`, `axis_rad`, all default 0. |
| `matrix` | 1 → 1 | `matrix`: a 2-by-2 array of complex amplitudes. |

An NPBS uses `T = diag(cos(mixing_x), cos(mixing_y))` and
`R = i diag(sin(mixing_x), sin(mixing_y))`; its outputs are `T A + R B` and
`R A + T B`. Thus `mixing_rad = pi/4` means 50/50 **power** splitting, not a
50% field amplitude. This is a constrained unitary model. Arbitrary measured
reflection/transmission phases would require a general two-port scattering
element, not independent arbitrary changes to this unitary convention.

A rotated diattenuator is `R(axis).T @ diag(ax exp(i px), ay exp(i py)) @ R(axis)`,
where `R(axis) = [[cos(axis), -sin(axis)], [sin(axis), cos(axis)]]`. A retarder sets
`ax = ay = 1` and `(px, py) = (-retardance/2, retardance/2)`. This preserves the
historical `Retarder` axis sign; check its relationship to your mount's rotation.

Passive one-path elements must have largest singular value at most one.
Explicit `"allow_gain": true` permits relative fitted scales or modeled gain.
The report identifies these elements. The ordinary hybrid-MZI flow is passive;
the historical fit uses a separately named flow that allows relative scales.

## Add a measured optic to the hybrid MZI

Copy `configs/flows/hybrid_mzi.json`. To place a measured optic after the C-arm
retarder and before the final NPBS, insert this element immediately before NPBS2:

```json
{
  "name": "measured_C_optic",
  "type": "diattenuator",
  "inputs": ["C_after_phi2"],
  "outputs": ["C_measured"],
  "amplitude_x": "c_tx",
  "amplitude_y": "c_ty",
  "phase_x_rad": "c_phase_x",
  "phase_y_rad": "c_phase_y"
}
```

Change NPBS2's first input from `C_after_phi2` to `C_measured`. Add `c_tx`,
`c_ty`, `c_phase_x`, and `c_phase_y` to a copy of the parameter file. The same
runner will derive the new outputs, including any cross-phase dependence.

For a supplied Jones matrix, replace that element's type/settings with:

```json
{
  "name": "measured_C_optic",
  "type": "matrix",
  "inputs": ["C_after_phi2"],
  "outputs": ["C_measured"],
  "matrix": [["jxx", "jxy"], ["jyx", "jyy"]]
}
```

Define the four complex entries in the parameter file in the same `(x,y)` basis.

## Phase controls and scans

List phase controls in the flow, e.g. `"phases": ["phi1", "phi2"]`. Every
`phase` element applies `exp(i*(phase + offset))` to both polarizations of its
input. A control can drive more than one phase element; repeated traversal
produces higher integer harmonics automatically.

The parameter file must provide all and only those controls:

```json
"phase_values": {"phi1": 0.73, "phi2": 1.18},
"scan": {
  "points": 81,
  "start_rad": 0,
  "stop_rad": "2*pi",
  "dark_threshold": 1e-12
}
```

The scan section is optional; the values above are the defaults. Use 9–501
points. The engine supports up to four controls and 256 coherent field modes;
these bounds keep generated studies manageable. All control pairs receive a
2D scan, with other controls held fixed. For a static layout use an empty
`phases` list and empty `phase_values` object.

`dark_threshold` is absolute **intensity**, not field amplitude. Set it according
to the scale of your inputs. Exact zero fields have undefined polarization even
with a zero threshold. Circular polarization has undefined ellipse azimuth;
S1-polar states have undefined sphere azimuth.

## Python use

With `PYTHONPATH=python`:

```python
from pathlib import Path
import numpy as np
from field_propogation.configuration import load_configuration
from field_propogation.propagation import compile_network
from field_propogation.observables import describe

config = load_configuration(Path("my_flow.json"), Path("my_params.json"))
network = compile_network(config)
fields = network.evaluate({"phi1": np.linspace(0, 2*np.pi, 1001)})
output = describe(fields["E"], dark_threshold=config.scan["dark_threshold"])
```

Omitted phases use the configured operating point. Arrays follow NumPy
broadcasting. `network.evaluate(checkpoints=True)` returns all named fields.
`network.stokes_harmonics("E")` returns analytic Fourier coefficients for
unnormalized `Q0, Q1, Q2, Q3`; the report derives normalized `s = Q[1:]/Q0`.

## Measured parameter sets (schema version 2)

Use `acquire.py build` to generate these from registered independent readings.
Every `values` entry needs a matching `provenance` entry: `status`, `units`,
`quantity`, `plane`, `sources` (record IDs), `uncertainty` and `notes`.
Measured/derived entries need a nonnegative standard uncertainty and source IDs.
Statuses are `measured`, `derived`, `convention`, `assumed`, `nuisance` and
`fitted_nuisance`. Nuisances need finite bounds and a selected real value;
fitted nuisances additionally need an identifiability-report reference. The
active acquisition builder does not fit nuisances. Input normalization is
`{"plane":"P0_before_PBS","field_units":"sqrt(W)"}`.

Schema-1 exploratory examples remain readable, but reports explicitly label
them as having no enforced independent-measurement provenance. A schema-2
parameter file is not proof of physical validation; assumptions and unresolved
nuisances are visible. See the [acquisition protocol](measurement_acquisition.md)
for uncertainty limitations and detector-aware prediction.

## Measured multiport optics

`splitter` takes one Jones input and two outputs (4×2 matrix). `scattering`
takes two inputs and two outputs (4×4 matrix). Rows/columns are ordered
`port0_x, port0_y, port1_x, port1_y`; the one-input case has only two columns.
Supply either `matrix` (complex expressions/parameters) or both `amplitudes`
and `phases_rad` of the same shape. Magnitudes are FIELD coefficients. H/V and
each incident/output port can differ; arbitrary cross-polarization entries are
supported by the full matrix form. The entire matrix must be passive, checked
by its largest singular value, not just by individual coefficient magnitudes.
Do not enable `allow_gain` to hide inconsistent independent measurements.

The acquisition builder assumes diagonal H/V splitter blocks, measures their
magnitudes separately, and exposes unmeasured phases as nuisance parameters.
A measured cross-polarizing splitter requires full matrix characterization.
Each acquired section's 2×2 Jones matrix includes its transmission once.

Spatial overlap and fiber projection are separate detector settings, described
in the acquisition protocol and implemented in `detection.py`. They are applied
by `acquire.py predict`, not silently folded into the coherent Jones report.
