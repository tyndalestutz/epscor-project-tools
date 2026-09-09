import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, TextBox

from sympy import symbols, exp, I, simplify, lambdify, sympify, pi

# =============================================================================
# 1. SYMBOLIC SETUP
# =============================================================================

phi1, phi2, delta = symbols('phi1 phi2 delta', real=True)
ax, ay = symbols('ax ay', real=True, positive=True)

Ex = ax * (exp(I*(phi1 + delta)) - exp(I*(phi1 + phi2 + delta))) / 2
Ey = I * ay

Ex, Ey = simplify(Ex), simplify(Ey)

E_func = lambdify((phi1, phi2, delta, ax, ay), (Ex, Ey), 'numpy')


# =============================================================================
# 2. STOKES
# =============================================================================

def jones_to_stokes(Ex, Ey):
    S0 = np.abs(Ex)**2 + np.abs(Ey)**2
    S1 = np.abs(Ex)**2 - np.abs(Ey)**2
    S2 = 2*np.real(Ex*np.conj(Ey))
    S3 = -2*np.imag(Ex*np.conj(Ey))
    return np.array([S1, S2, S3]) / S0


# =============================================================================
# 3. SAFE PARSER (THIS IS THE IMPORTANT ADDITION)
# =============================================================================

def parse_expr(text):
    """
    Safely parse pi-based expressions like:
    'pi/2', '3*pi/4', 'pi', '0.1'
    """
    try:
        return float(sympify(text, locals={"pi": pi}))
    except Exception:
        return 0.0


# =============================================================================
# 4. INITIAL VALUES
# =============================================================================

ax0, ay0 = 1.0, 1.0

phi10, phi20, delta0 = 0.0, 0.0, 0.0


# =============================================================================
# 5. FIGURE
# =============================================================================

fig = plt.figure(figsize=(10, 4))
ax_sphere = fig.add_subplot(1, 2, 1, projection='3d')
ax_xy = fig.add_subplot(1, 2, 2)

plt.subplots_adjust(bottom=0.35)


# Sphere
u = np.linspace(0, 2*np.pi, 30)
v = np.linspace(0, np.pi, 30)
ax_sphere.plot_wireframe(
    np.outer(np.cos(u), np.sin(v)),
    np.outer(np.sin(u), np.sin(v)),
    np.outer(np.ones_like(u), np.cos(v)),
    alpha=0.2
)

point, = ax_sphere.plot([0], [0], [1], 'ro')


# XY plot
line_xy, = ax_xy.plot([], [])
ax_xy.set_aspect('equal')
ax_xy.set_xlim(-2, 2)
ax_xy.set_ylim(-2, 2)


# =============================================================================
# 6. SLIDERS
# =============================================================================

s_phi1 = Slider(plt.axes([0.2, 0.25, 0.6, 0.02]), 'phi1', 0, np.pi, valinit=phi10)
s_phi2 = Slider(plt.axes([0.2, 0.20, 0.6, 0.02]), 'phi2', 0, np.pi, valinit=phi20)
s_delta = Slider(plt.axes([0.2, 0.15, 0.6, 0.02]), 'delta', 0, np.pi, valinit=delta0)


# =============================================================================
# 7. TEXT INPUT BOXES (NEW)
# =============================================================================

box_phi1 = TextBox(plt.axes([0.2, 0.10, 0.2, 0.03]), "φ1 expr", initial="0")
box_phi2 = TextBox(plt.axes([0.45, 0.10, 0.2, 0.03]), "φ2 expr", initial="0")
box_delta = TextBox(plt.axes([0.7, 0.10, 0.2, 0.03]), "δ expr", initial="0")


# =============================================================================
# 8. UPDATE
# =============================================================================

def update(_=None):

    # sliders (numeric control)
    phi1_v = s_phi1.val
    phi2_v = s_phi2.val
    delta_v = s_delta.val

    # override with text if present (priority)
    phi1_txt = parse_expr(box_phi1.text)
    phi2_txt = parse_expr(box_phi2.text)
    delta_txt = parse_expr(box_delta.text)

    # if user typed something nonzero, override slider
    if box_phi1.text.strip():
        phi1_v = phi1_txt
    if box_phi2.text.strip():
        phi2_v = phi2_txt
    if box_delta.text.strip():
        delta_v = delta_txt

    Ex, Ey = E_func(phi1_v, phi2_v, delta_v, ax0, ay0)
    Ex, Ey = Ex + 0j, Ey + 0j

    S = jones_to_stokes(Ex, Ey)

    point.set_data([S[0]], [S[1]])
    point.set_3d_properties([S[2]])

    # ellipse
    t = np.linspace(0, 2*np.pi, 200)
    Ex_t = np.real(Ex * np.exp(1j*t))
    Ey_t = np.real(Ey * np.exp(1j*t))

    line_xy.set_data(Ex_t, Ey_t)

    fig.canvas.draw_idle()


s_phi1.on_changed(update)
s_phi2.on_changed(update)
s_delta.on_changed(update)

box_phi1.on_submit(update)
box_phi2.on_submit(update)
box_delta.on_submit(update)

update()
plt.show()