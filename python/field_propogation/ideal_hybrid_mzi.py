#!/usr/bin/env python3
"""Symbolic reference model for the ideal two-combiner hybrid MZI.

This module is deliberately independent of ``polarization_locking``.  It is
the agreed equal-amplitude baseline against which later data fitting can be
compared; it does not command hardware or alter the lock model.

Conventions
-----------
Jones basis is ``(x, y)``.  The 50/50 non-polarizing beam splitter uses
``t=1/sqrt(2)`` and ``r=i/sqrt(2)``.  The reported Stokes convention follows
the PAX/locking code:

    S1 = (|Ey|^2 - |Ex|^2) / S0
    S2 = 2 Re(Ex Ey*) / S0
    S3 = 2 Im(Ex Ey*) / S0

The element order is documented in :data:`PROPAGATION_ORDER` so it can be
checked directly against the bench before any non-ideal model is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

import sympy as sp


# Symbols remain public so a notebook can import this module and substitute
# experimental values without re-deriving the field propagation.
phi1, phi2, delta = sp.symbols("phi_1 phi_2 delta", real=True)
ax, ay = sp.symbols("a_x a_y", real=True, nonnegative=True)
I = sp.I

T = 1 / sp.sqrt(2)
R = I / sp.sqrt(2)

PROPAGATION_ORDER: tuple[str, ...] = (
    "Input Jones field Ein = (a_x exp(i delta), a_y)^T.",
    "PBS: transmitted x component is A_before_phi1; reflected y component is B.",
    "phi1: scalar phase exp(i phi1) on A_before_phi1, producing A_after_phi1.",
    "NPBS 1: C is the transmitted output; D is the reflected output.",
    "phi2: scalar phase exp(i phi2) on intermediate arm C only.",
    "NPBS 2: E is the transmitted output; F is the reflected output.",
)


@dataclass(frozen=True)
class SymbolicModel:
    """All named fields and observables of the ideal propagation."""

    fields: Mapping[str, sp.Matrix]
    intensity_e: sp.Expr
    intensity_f: sp.Expr
    total_intensity: sp.Expr
    stokes_e: Mapping[str, sp.Expr]
    u_e: sp.Expr
    v_e: sp.Expr
    coordinate_jacobian_e: sp.Matrix


@dataclass(frozen=True)
class PoincareCoordinates:
    """The full Jones-to-Poincare result for one optical field.

    ``u`` and ``v`` deliberately remain in their general inverse-trigonometric
    form.  This is the object to use after changing a component or fitting a
    non-ideal parameter: any phase coupling remains visible in the expressions
    rather than being hidden by an ideal-case substitution.
    """

    stokes: Mapping[str, sp.Expr]
    u: sp.Expr
    v: sp.Expr


def beam_splitter(field_a: sp.Matrix, field_b: sp.Matrix) -> tuple[sp.Matrix, sp.Matrix]:
    """Return ``(transmitted, reflected)`` NPBS outputs.

    The first input ``field_a`` is transmitted with ``t`` and reflected with
    ``r``. The second input obtains the reciprocal coefficients, yielding the
    unitary scattering convention

    ``transmitted = t*A + r*B`` and ``reflected = r*A + t*B``.
    """
    return (sp.simplify(T * field_a + R * field_b), sp.simplify(R * field_a + T * field_b))


def intensity(field: sp.Matrix) -> sp.Expr:
    """Total optical intensity in an orthogonal Jones basis."""
    return sp.simplify(sum(sp.conjugate(component) * component for component in field))


def normalized_stokes(field: sp.Matrix) -> dict[str, sp.Expr]:
    """Return the normalized PAX/locking Stokes convention for ``field``."""
    ex, ey = field
    s0 = intensity(field)
    return {
        "S0": s0,
        "S1": sp.simplify((sp.conjugate(ey) * ey - sp.conjugate(ex) * ex) / s0),
        "S2": sp.simplify(2 * sp.re(ex * sp.conjugate(ey)) / s0),
        "S3": sp.simplify(2 * sp.im(ex * sp.conjugate(ey)) / s0),
    }


def jones_to_poincare(field: sp.Matrix) -> PoincareCoordinates:
    """Derive Poincare coordinates from a Jones field using the PAX convention.

    The route is intentionally explicit::

        (Ex, Ey) -> (S0, S1, S2, S3) -> (u, v)
        u = atan2(S3, S2),  v = acos(S1)

    ``atan2`` and ``acos`` retain the correct coordinate branches.  Do not
    replace this with an ideal relation such as ``u = phi1 + delta`` when
    testing a modified field model.
    """
    stokes = normalized_stokes(field)
    return PoincareCoordinates(
        stokes=stokes,
        u=sp.atan2(stokes["S3"], stokes["S2"]),
        v=sp.acos(stokes["S1"]),
    )


def poincare_jacobian(
    field: sp.Matrix,
    parameters: tuple[sp.Symbol, ...] = (phi1, phi2),
) -> sp.Matrix:
    """Return the symbolic local map ``d(u, v) / d(parameters)``.

    Off-diagonal terms are the directly inspectable coordinate coupling terms.
    For example, with ``parameters=(phi1, phi2)``, ``J[0, 1]`` is the local
    phi2-to-u coupling and ``J[1, 0]`` is the local phi1-to-v coupling.
    """
    coordinates = jones_to_poincare(field)
    return sp.Matrix(
        [
            [sp.diff(coordinates.u, parameter) for parameter in parameters],
            [sp.diff(coordinates.v, parameter) for parameter in parameters],
        ]
    ).applyfunc(sp.simplify)


@lru_cache(maxsize=1)
def build_model() -> SymbolicModel:
    """Propagate the ideal layout and expose both final ports symbolically."""
    input_field = sp.Matrix((ax * sp.exp(I * delta), ay))
    arm_a_before_phi1 = sp.Matrix((ax * sp.exp(I * delta), 0))
    arm_a_after_phi1 = sp.simplify(sp.exp(I * phi1) * arm_a_before_phi1)
    arm_b = sp.Matrix((0, ay))
    arm_c, arm_d = beam_splitter(arm_a_after_phi1, arm_b)
    arm_c_after_phi2 = sp.simplify(sp.exp(I * phi2) * arm_c)
    port_e, port_f = beam_splitter(arm_c_after_phi2, arm_d)

    fields = {
        "Ein": input_field,
        # ``A`` remains an alias for the operational, post-phi1 field so
        # existing notebook-style access is unambiguous, while both physical
        # checkpoints remain available for arrangement verification.
        "A_before_phi1": arm_a_before_phi1,
        "A_after_phi1": arm_a_after_phi1,
        "A": arm_a_after_phi1,
        "B": arm_b,
        "C": arm_c,
        "D": arm_d,
        "C_after_phi2": arm_c_after_phi2,
        "E": sp.simplify(port_e),
        "F": sp.simplify(port_f),
    }
    coordinates_e = jones_to_poincare(fields["E"])
    return SymbolicModel(
        fields=fields,
        intensity_e=intensity(fields["E"]),
        intensity_f=intensity(fields["F"]),
        total_intensity=sp.simplify(intensity(fields["E"]) + intensity(fields["F"])),
        stokes_e=coordinates_e.stokes,
        u_e=coordinates_e.u,
        v_e=coordinates_e.v,
        coordinate_jacobian_e=poincare_jacobian(fields["E"]),
    )


def ideal_equal_amplitude_observables() -> dict[str, sp.Expr]:
    """Return the compact equal-amplitude result, normalized to ``ax=ay=1``.

    Every entry is derived from the propagated final field via
    :func:`jones_to_poincare`; no ideal ``u`` or ``v`` relation is inserted by
    hand.  The resulting ``atan2``/``acos`` expressions retain their branches.
    """
    model = build_model()
    equal = {ax: 1, ay: 1}
    final_field = sp.simplify(model.fields["E"].subs(equal))
    coordinates = jones_to_poincare(final_field)
    return {
        "E": final_field,
        "F": sp.simplify(model.fields["F"].subs(equal)),
        "I_E": intensity(final_field),
        "I_F": intensity(model.fields["F"].subs(equal)),
        "I_E_plus_I_F": sp.simplify(
            intensity(final_field) + intensity(model.fields["F"].subs(equal))
        ),
        "S0_E": coordinates.stokes["S0"],
        "S1_E": coordinates.stokes["S1"],
        "S2_E": coordinates.stokes["S2"],
        "S3_E": coordinates.stokes["S3"],
        "u_E": coordinates.u,
        "v_E": coordinates.v,
    }


def scalar_amplitude_port_intensities() -> dict[str, sp.Expr]:
    """Return the ideal port intensities before imposing equal amplitudes.

    This is still the same ideal optical layout: it only exposes why the
    constant-power result requires equal *effective* x/y amplitudes. It is the
    constrained scalar-amplitude extension intended for the future fit.
    """
    return {
        "I_E": sp.trigsimp(ax**2 * sp.sin(phi2 / 2) ** 2 + ay**2 * sp.cos(phi2 / 2) ** 2),
        "I_F": sp.trigsimp(ax**2 * sp.cos(phi2 / 2) ** 2 + ay**2 * sp.sin(phi2 / 2) ** 2),
        "I_E_plus_I_F": sp.simplify(ax**2 + ay**2),
    }


def latex_reference() -> dict[str, str]:
    """Provide LaTex strings for a notebook, report, or manual arrangement check."""
    return {name: sp.latex(value) for name, value in ideal_equal_amplitude_observables().items()}


def main() -> None:
    """Print a compact symbolic reference at the command line."""
    model = build_model()
    print("Ideal hybrid-MZI reference (no fit parameters)")
    print("\nElement order:")
    for index, step in enumerate(PROPAGATION_ORDER, start=1):
        print(f"  {index}. {step}")
    print("\nFinal Jones fields:")
    print("  E =", sp.simplify(model.fields["E"]))
    print("  F =", sp.simplify(model.fields["F"]))
    print("\nEqual-amplitude observables (a_x=a_y=1):")
    for name, expression in ideal_equal_amplitude_observables().items():
        print(f"  {name} = {expression}")
    print("\nSame ideal layout with symbolic effective amplitudes:")
    for name, expression in scalar_amplitude_port_intensities().items():
        print(f"  {name} = {expression}")
    equal_field = model.fields["E"].subs({ax: 1, ay: 1})
    print("\nDerived ideal coordinate Jacobian: d(u_E, v_E)/d(phi_1, phi_2)")
    print(" ", poincare_jacobian(equal_field))


if __name__ == "__main__":
    main()
