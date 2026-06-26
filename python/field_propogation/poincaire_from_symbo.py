import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
from sympy import symbols, exp, I, simplify, lambdify

# =============================================================================
# 1. SYMBOLIC SETUP
# =============================================================================

phi1, phi2, delta = symbols('phi1 phi2 delta', real=True)
ax, ay = symbols('ax ay', real=True, positive=True)

# Jones vector (your expression)
Ex = ax * (exp(I*(phi1 + delta)) - exp(I*(phi1 + phi2 + delta))) / 2
Ey = I * ay

J = simplify(Ex), simplify(Ey)

# Lambdify (numeric fast evaluation)
E_func = lambdify((phi1, phi2, delta, ax, ay), J, 'numpy')


# =============================================================================
# 2. JONES → STOKES
# =============================================================================

def jones_to_stokes(Ex, Ey):
    S0 = np.abs(Ex)**2 + np.abs(Ey)**2
    S1 = np.abs(Ex)**2 - np.abs(Ey)**2
    S2 = 2*np.real(Ex*np.conj(Ey))
    S3 = -2*np.imag(Ex*np.conj(Ey))

    return np.array([S1, S2, S3]) / S0


# =============================================================================
# 3. POLARIZATION ELLIPSE (XY FIELD)
# =============================================================================

def polarization_ellipse(Ex, Ey, n=200):
    t = np.linspace(0, 2*np.pi, n)

    # time evolution of real field
    Ex_t = np.real(Ex * np.exp(1j*t))
    Ey_t = np.real(Ey * np.exp(1j*t))

    return Ex_t, Ey_t


# =============================================================================
# 4. INITIAL PARAMETERS
# =============================================================================

ax0, ay0 = 1.0, 1.0
phi10, phi20, delta0 = 0.0, 0.0, 0.0

Ex0, Ey0 = E_func(phi10, phi20, delta0, ax0, ay0)
Ex0, Ey0 = Ex0 + 0j, Ey0 + 0j

S = jones_to_stokes(Ex0, Ey0)


# =============================================================================
# 5. FIGURE SETUP
# =============================================================================

fig = plt.figure(figsize=(10, 4))

ax_sphere = fig.add_subplot(1, 2, 1, projection='3d')
ax_xy = fig.add_subplot(1, 2, 2)

plt.subplots_adjust(bottom=0.25)


# Poincaré sphere (unit sphere wireframe)
u = np.linspace(0, 2*np.pi, 30)
v = np.linspace(0, np.pi, 30)
x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))

ax_sphere.plot_wireframe(x, y, z, alpha=0.2)

point, = ax_sphere.plot([S[0]], [S[1]], [S[2]], 'ro')


# XY polarization ellipse
line_xy, = ax_xy.plot([], [], lw=2)
ax_xy.set_xlim(-2, 2)
ax_xy.set_ylim(-2, 2)
ax_xy.set_title("Polarization ellipse")
ax_xy.set_aspect('equal')


# =============================================================================
# 6. SLIDER SETUP
# =============================================================================

ax_phi1 = plt.axes([0.2, 0.15, 0.6, 0.02])
ax_phi2 = plt.axes([0.2, 0.10, 0.6, 0.02])
ax_delta = plt.axes([0.2, 0.05, 0.6, 0.02])

s_phi1 = Slider(ax_phi1, 'phi1', 0, np.pi, valinit=phi10)
s_phi2 = Slider(ax_phi2, 'phi2', 0, np.pi, valinit=phi20)
s_delta = Slider(ax_delta, 'delta', 0, np.pi, valinit=delta0)


# =============================================================================
# 7. UPDATE FUNCTION
# =============================================================================

def update(val):
    phi1_v = s_phi1.val
    phi2_v = s_phi2.val
    delta_v = s_delta.val

    Ex, Ey = E_func(phi1_v, phi2_v, delta_v, ax0, ay0)
    Ex, Ey = Ex + 0j, Ey + 0j

    S = jones_to_stokes(Ex, Ey)

    # update sphere point
    point.set_data([S[0]], [S[1]])
    point.set_3d_properties([S[2]])

    # update ellipse
    Ex_t, Ey_t = polarization_ellipse(Ex, Ey)
    line_xy.set_data(Ex_t, Ey_t)

    fig.canvas.draw_idle()


s_phi1.on_changed(update)
s_phi2.on_changed(update)
s_delta.on_changed(update)

update(None)

plt.show()