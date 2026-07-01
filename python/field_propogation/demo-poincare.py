import numpy as np
import matplotlib.pyplot as plt

# =========================================================
# Stokes definition
# =========================================================
def stokes(phi1, phi2):
    s1 = np.sin(phi2 / 2) ** 2 - 1
    s2 = -2 * np.sin(phi2 / 2) * np.cos(phi1 + phi2 / 2)
    s3 =  2 * np.sin(phi2 / 2) * np.sin(phi1 + phi2 / 2)
    return np.array([s1, s2, s3])


def normalize(v):
    return v / (np.linalg.norm(v, axis=0) + 1e-12)


# =========================================================
# Sphere mesh
# =========================================================
u = np.linspace(0, 2*np.pi, 80)
v = np.linspace(0, np.pi, 80)

x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))


# =========================================================
# Figure
# =========================================================
fig = plt.figure(figsize=(8, 7))
ax = fig.add_subplot(111, projection='3d')

ax.plot_wireframe(x, y, z, color="gray", linewidth=0.4, alpha=0.25)


# =========================================================
# Stokes cloud
# =========================================================
phi1 = np.linspace(-np.pi, np.pi, 120)
phi2 = np.linspace(0, np.pi, 120)
P1, P2 = np.meshgrid(phi1, phi2)

S = stokes(P1, P2)
S = normalize(S)

ax.scatter(S[0], S[1], S[2], s=1, alpha=0.25, color="navy")


# =========================================================
# SAFE CLEANUP (DO NOT fully delete axis artists in 3D Qt backend)
# =========================================================
ax.set_xticks([])
ax.set_yticks([])
ax.set_zticks([])

ax.grid(False)

# hide panes safely (DO NOT use set_visible(False))
ax.xaxis.pane.set_facecolor((1, 1, 1, 0))
ax.yaxis.pane.set_facecolor((1, 1, 1, 0))
ax.zaxis.pane.set_facecolor((1, 1, 1, 0))

ax.xaxis.pane.set_edgecolor((1, 1, 1, 0))
ax.yaxis.pane.set_edgecolor((1, 1, 1, 0))
ax.zaxis.pane.set_edgecolor((1, 1, 1, 0))


# =========================================================
# Axes limits
# =========================================================
R = 1.25
ax.set_xlim([-R, R])
ax.set_ylim([-R, R])
ax.set_zlim([-R, R])


# =========================================================
# Axes lines
# =========================================================
ax.plot([-R, R], [0, 0], [0, 0], color="black", lw=1.5)
ax.plot([0, 0], [-R, R], [0, 0], color="black", lw=1.5)
ax.plot([0, 0], [0, 0], [-R, R], color="black", lw=1.5)


# =========================================================
# Arrowheads
# =========================================================
def arrowhead(end, direction, scale=0.18):
    d = np.array(direction)
    d = d / np.linalg.norm(d)

    base = np.array(end) - d * scale

    ax.plot([base[0], end[0]],
            [base[1], end[1]],
            [base[2], end[2]],
            color="black", lw=2.5)

arrowhead([R, 0, 0], [1, 0, 0])
arrowhead([0, R, 0], [0, 1, 0])
arrowhead([0, 0, R], [0, 0, 1])

arrowhead([-R, 0, 0], [-1, 0, 0])
arrowhead([0, -R, 0], [0, -1, 0])
arrowhead([0, 0, -R], [0, 0, -1])


# =========================================================
# Labels
# =========================================================
ax.text(R*1.08, 0, 0, "S1", fontsize=13)
ax.text(0, R*1.08, 0, "S2", fontsize=13)
ax.text(0, 0, R*1.08, "S3", fontsize=13)


# =========================================================
# View
# =========================================================
ax.view_init(elev=20, azim=35)
ax.set_box_aspect([1, 1, 1])

ax.set_title("Clean Poincaré Sphere (Stokes Space)", pad=18)

# IMPORTANT: no tight_layout for 3D
plt.subplots_adjust(0, 0, 1, 1)

plt.show()