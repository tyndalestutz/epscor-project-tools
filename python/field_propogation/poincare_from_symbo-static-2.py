import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from sympy import symbols, exp, I, lambdify, simplify

# =============================================================================
# 1. SYMBOLIC JONES VECTOR (UPDATED SYSTEM)
# =============================================================================

phi1, phi2, phi3, delta = symbols('phi1 phi2 phi3 delta', real=True)
ax, ay = symbols('ax ay', real=True, positive=True)

# X arm (unchanged interferometric structure)
Ex = ax * (exp(I*(phi1 + delta)) - exp(I*(phi1 + phi2 + delta))) / 2

# Y arm (NEW: balanced EOM / interferometric modulation)
Ey = I * ay * (exp(I*phi3) + 1) / 2

Ex = simplify(Ex)
Ey = simplify(Ey)

jones_func = lambdify((phi1, phi2, phi3, delta, ax, ay), (Ex, Ey), 'numpy')


# =============================================================================
# 2. JONES -> STOKES
# =============================================================================

def jones_to_stokes(Ex, Ey):
    S0 = np.abs(Ex)**2 + np.abs(Ey)**2
    S1 = np.abs(Ex)**2 - np.abs(Ey)**2
    S2 = 2*np.real(Ex * np.conj(Ey))
    S3 = -2*np.imag(Ex * np.conj(Ey))
    return np.array([S1, S2, S3]) / S0


# =============================================================================
# 3. PARAMETER SPACE (DOFs)
# =============================================================================

phi2_vals  = np.linspace(0, 2*np.pi, 90)
phi3_vals  = np.linspace(0, 2*np.pi, 90)
delta_vals = np.linspace(0, 2*np.pi, 60)

ax0 = 1.0
ay0 = 1.0
phi1_0 = 0.0


# =============================================================================
# 4. SWEEP PARAMETER SPACE
# =============================================================================

S1_list, S2_list, S3_list = [], [], []

for phi2_v in phi2_vals:
    for phi3_v in phi3_vals:
        for delta_v in delta_vals:

            Ex_val, Ey_val = jones_func(
                phi1_0, phi2_v, phi3_v, delta_v, ax0, ay0
            )

            Ex_val = complex(Ex_val)
            Ey_val = complex(Ey_val)

            S = jones_to_stokes(Ex_val, Ey_val)

            S1_list.append(S[0])
            S2_list.append(S[1])
            S3_list.append(S[2])


S1 = np.array(S1_list)
S2 = np.array(S2_list)
S3 = np.array(S3_list)


# =============================================================================
# 5. NORMALIZE TO UNIT SPHERE
# =============================================================================

norm = np.sqrt(S1**2 + S2**2 + S3**2)
S1, S2, S3 = S1/norm, S2/norm, S3/norm


# =============================================================================
# 6. PLOTTING
# =============================================================================

fig = plt.figure(figsize=(10, 7))
ax = fig.add_subplot(111, projection='3d')

# Poincaré sphere wireframe
u = np.linspace(0, 2*np.pi, 50)
v = np.linspace(0, np.pi, 50)

x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))

ax.plot_wireframe(x, y, z, alpha=0.15, linewidth=0.5)

# reachable set
ax.scatter(S1, S2, S3, s=1, alpha=0.25)

ax.set_title("Poincaré Sphere Coverage (Extended EOM in Orthogonal Arm)")
ax.set_xlabel("S1")
ax.set_ylabel("S2")
ax.set_zlabel("S3")

plt.tight_layout()
plt.show()