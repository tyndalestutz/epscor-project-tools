import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
from sympy import *

###############################################################################
# YOUR OPTICAL SYSTEM (unchanged conceptually)
###############################################################################

phi_EOM1, phi_EOM2, delta = symbols('phi_EOM1 phi_EOM2 delta', real=True)
ax, ay = symbols('a_x a_y', real=True)

t = 1/sqrt(2)
r = I/sqrt(2)

Ein = Matrix([
    [ax * exp(I * delta)],
    [ay]
])

def Mirror():
    return eye(2)

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
    EC = simplify(t*EA + r*EB)
    ED = simplify(r*EA + t*EB)
    return EC, ED

###############################################################################
# SYSTEM EVALUATION (NUMERIC WRAPPER)
###############################################################################

def system(phi1, phi2, delta_v):

    subs = {
        phi_EOM1: phi1,
        phi_EOM2: phi2,
        delta: delta_v,
        ax: 1.0,
        ay: 1.0
    }

    E0 = Ein.subs(subs)

    armA = propagate(E0, PBS_T(), Mirror(), EOM(phi_EOM1))
    armB = propagate(E0, PBS_R(), Mirror())

    armC, armD = BS(armA, armB)

    armC = propagate(armC, Mirror(), EOM(phi_EOM2))
    armD = propagate(armD, Mirror())

    armF = simplify(r*armD + t*armC)

    # CRITICAL: force full evaluation to numeric expressions
    armF = armF.subs(subs).evalf()

    return armF


###############################################################################
# STOKES
###############################################################################

def stokes(E):
    E = E.evalf()   # <-- key fix

    Ex = complex(E[0])
    Ey = complex(E[1])

    S0 = np.abs(Ex)**2 + np.abs(Ey)**2
    S1 = np.abs(Ex)**2 - np.abs(Ey)**2
    S2 = 2*np.real(Ex*np.conj(Ey))
    S3 = -2*np.imag(Ex*np.conj(Ey))

    return np.array([S0, S1, S2, S3])

def normalize(S):
    return np.array([S[1], S[2], S[3]]) / (S[0] + 1e-12)

###############################################################################
# POINCARE PLOT SETUP
###############################################################################

fig = plt.figure()
ax3d = fig.add_subplot(111, projection='3d')

u = np.linspace(0, 2*np.pi, 30)
v = np.linspace(0, np.pi, 15)

x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))

ax3d.plot_wireframe(x, y, z, color='lightgray', alpha=0.3)

point, = ax3d.plot([0], [0], [0], 'ro', markersize=10)

ax3d.set_xlim([-1,1])
ax3d.set_ylim([-1,1])
ax3d.set_zlim([-1,1])

ax3d.set_xlabel("S1")
ax3d.set_ylabel("S2")
ax3d.set_zlabel("S3")

###############################################################################
# SLIDERS
###############################################################################

ax_phi1 = plt.axes([0.25, 0.1, 0.65, 0.03])
ax_phi2 = plt.axes([0.25, 0.06, 0.65, 0.03])
ax_delta = plt.axes([0.25, 0.02, 0.65, 0.03])

s_phi1 = Slider(ax_phi1, 'phi1', 0, np.pi, valinit=0.5)
s_phi2 = Slider(ax_phi2, 'phi2', 0, np.pi, valinit=0.5)
s_delta = Slider(ax_delta, 'delta', 0, np.pi, valinit=0.5)

###############################################################################
# UPDATE FUNCTION
###############################################################################

def update(val):

    E = system(s_phi1.val, s_phi2.val, s_delta.val)

    S = stokes(E)
    p = normalize(S)

    point.set_data([p[0]], [p[1]])
    point.set_3d_properties([p[2]])

    fig.canvas.draw_idle()

s_phi1.on_changed(update)
s_phi2.on_changed(update)
s_delta.on_changed(update)

update(None)

plt.show()