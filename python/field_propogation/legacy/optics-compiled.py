import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider

from sympy import (
    symbols, Matrix, exp, I, simplify, lambdify, sqrt
)

###############################################################################
# 1. SYMBOLS
###############################################################################

phi1, phi2, delta = symbols('phi1 phi2 delta', real=True)
ax, ay = symbols('ax ay', real=True)

t = 1/sqrt(2)
r = I/sqrt(2)

Ein = Matrix([
    [ax * exp(I * delta)],
    [ay]
])

###############################################################################
# 2. ELEMENTS
###############################################################################

def Mirror():
    return Matrix([[1,0],[0,1]])

def PBS_T():
    return Matrix([[1,0],[0,0]])

def PBS_R():
    return Matrix([[0,0],[0,1]])

def EOM(phi):
    return Matrix([
        [exp(I*phi),0],
        [0,1]
    ])

def propagate(E, *elements):
    for M in elements:
        E = simplify(M * E)
    return E

def BS(EA, EB):
    return t*EA + r*EB, r*EA + t*EB

###############################################################################
# 3. SYSTEM BUILD
###############################################################################

def build_system():
    E0 = Ein

    armA = propagate(E0, PBS_T(), Mirror(), EOM(phi1))
    armB = propagate(E0, PBS_R(), Mirror())

    armC, armD = BS(armA, armB)

    armC = propagate(armC, Mirror(), EOM(phi2))
    armD = propagate(armD, Mirror())

    armF = t*armD + r*armC

    return armF

armF_sym = build_system()

###############################################################################
# 4. LAMBDIFY (IMPORTANT: force scalar mode)
###############################################################################

E_func = lambdify(
    (phi1, phi2, delta, ax, ay),
    armF_sym,
    modules="numpy"
)

###############################################################################
# 5. STOKES (ROBUST FIXED)
###############################################################################

def stokes(E):

    # FORCE FULL FLATTENING FIRST (CRITICAL FIX)
    E = np.asarray(E, dtype=complex).reshape(-1)

    Ex = E[0]
    Ey = E[1]

    S0 = np.abs(Ex)**2 + np.abs(Ey)**2
    S1 = np.abs(Ex)**2 - np.abs(Ey)**2
    S2 = 2*np.real(Ex*np.conj(Ey))
    S3 = -2*np.imag(Ex*np.conj(Ey))

    return np.array([S0, S1, S2, S3])

def normalize(S):
    S = np.asarray(S, dtype=float).reshape(-1)
    return S[1:4] / (S[0] + 1e-12)

###############################################################################
# 6. PLOT SETUP
###############################################################################

fig = plt.figure(figsize=(10,5))

ax3d = fig.add_subplot(121, projection='3d')
ax2d = fig.add_subplot(122)

# sphere
u = np.linspace(0, 2*np.pi, 30)
v = np.linspace(0, np.pi, 15)

x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))

ax3d.plot_wireframe(x, y, z, color='lightgray', alpha=0.3)

point3d, = ax3d.plot([0],[0],[0],'ro')
point2d, = ax2d.plot([0],[0],'ro')

ax3d.set_xlim([-1,1])
ax3d.set_ylim([-1,1])
ax3d.set_zlim([-1,1])

ax3d.set_xlabel("S1")
ax3d.set_ylabel("S2")
ax3d.set_zlabel("S3")

ax2d.set_xlim([-1,1])
ax2d.set_ylim([-1,1])
ax2d.set_xlabel("S1")
ax2d.set_ylabel("S2")
ax2d.set_title("S1 vs S2")

###############################################################################
# 7. SLIDERS
###############################################################################

ax_phi1 = plt.axes([0.25, 0.1, 0.6, 0.03])
ax_phi2 = plt.axes([0.25, 0.06, 0.6, 0.03])
ax_delta = plt.axes([0.25, 0.02, 0.6, 0.03])

s_phi1 = Slider(ax_phi1, "phi1", 0, np.pi, valinit=0.5)
s_phi2 = Slider(ax_phi2, "phi2", 0, np.pi, valinit=0.5)
s_delta = Slider(ax_delta, "delta", 0, np.pi, valinit=0.5)

###############################################################################
# 8. UPDATE LOOP (FIXED)
###############################################################################

def update(val):

    E = E_func(
        float(s_phi1.val),
        float(s_phi2.val),
        float(s_delta.val),
        1.0,
        1.0
    )

    S = stokes(E)
    p = normalize(S)

    p = np.asarray(p, dtype=float).reshape(-1)

    point3d.set_data([p[0]], [p[1]])
    point3d.set_3d_properties([p[2]])

    point2d.set_data([p[0]], [p[1]])

    fig.canvas.draw_idle()

###############################################################################
# 9. CONNECT
###############################################################################

s_phi1.on_changed(update)
s_phi2.on_changed(update)
s_delta.on_changed(update)

update(None)

plt.show()