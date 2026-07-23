#!/usr/bin/env python3

import yaqc
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D
import signal
import sys
import time


# =========================
# USER SETTINGS
# =========================

HOST = "localhost"
PORT = 38400

UPDATE_HZ = 10          # plot refresh rate
MAX_POINTS = 500        # history length


# =========================
# GLOBAL STATE
# =========================

running = True

times = []
thetas = []
etas = []
dops = []

S1_history = []
S2_history = []
S3_history = []


# =========================
# CONNECT TO YAQD
# =========================

print("Connecting to PAX1000...")

c = yaqc.Client(
    host=HOST,
    port=PORT
)

print(c)
print("Channels:")
print(c.get_channel_names())


# =========================
# CLEAN EXIT
# =========================

def shutdown(sig=None, frame=None):
    global running

    print("\nStopping acquisition...")
    running = False

    plt.close("all")

    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)


# =========================
# POLARIZATION MATH
# =========================

def poincare_coordinates(theta, eta, dop=1):

    S1 = np.cos(2*eta)*np.cos(2*theta)
    S2 = np.cos(2*eta)*np.sin(2*theta)
    S3 = np.sin(2*eta)

    # return dop*S1, dop*S2, dop*S3 
    return S1, S2, S3 # hopefully scaled properly



# =========================
# FIGURE
# =========================

fig = plt.figure(
    figsize=(12,6),
    facecolor="#808080"
)



# =========================
# POINCARE SPHERE
# =========================

# ---- Poincare sphere ----

ax1 = fig.add_subplot(
    121,
    projection="3d",
    facecolor="#808080"
)

ax1.set_title("Poincaré Sphere")


# sphere surface

u = np.linspace(0, 2*np.pi, 80)
v = np.linspace(0, np.pi, 40)

x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones(np.size(u)), np.cos(v))

ax1.plot_surface(
    x,
    y,
    z,
    color="#c0c0c0",
    alpha=0.95,
    linewidth=0,
    shade=True
)


# very light grid overlay

for angle in np.linspace(0, np.pi, 12):
    ax1.plot(
        np.cos(u)*np.sin(angle),
        np.sin(u)*np.sin(angle),
        np.cos(angle)*np.ones_like(u),
        color="black",
        alpha=0.08,
        linewidth=0.5
    )


# ==========================
# DARK EQUATORIAL AXES
# ==========================

circle = np.linspace(0,2*np.pi,400)

# S1 = 0 plane (YZ circle)

ax1.plot(
    np.zeros_like(circle),
    np.cos(circle),
    np.sin(circle),
    color="black",
    linewidth=1.5,
    alpha=0.7
)


# S2 = 0 plane (XZ circle)

ax1.plot(
    np.cos(circle),
    np.zeros_like(circle),
    np.sin(circle),
    color="black",
    linewidth=1.5,
    alpha=0.7
)


# S3 = 0 plane (XY equator)

ax1.plot(
    np.cos(circle),
    np.sin(circle),
    np.zeros_like(circle),
    color="black",
    linewidth=1.5,
    alpha=0.7
)



# measurement point

point = ax1.scatter(
    [0],
    [0],
    [0],
    s=60,
    color="red",
    edgecolors="black",
    linewidths=0.5,
    depthshade=False
)

# trajectory trace on sphere
trace, = ax1.plot(
    [],
    [],
    [],
    color="red",
    linewidth=1.5,
    alpha=0.8
)


# ==========================
# SPHERE LABELS
# ==========================

label_offset = 1.15

ax1.text(
    label_offset,
    0,
    0,
    "S1",
    fontsize=9
)

ax1.text(
    0,
    label_offset,
    0,
    "S2",
    fontsize=9
)

ax1.text(
    0,
    0,
    label_offset,
    "S3",
    fontsize=9
)


# negative axes

ax1.text(
    -label_offset,
    0,
    0,
    "-S1",
    fontsize=8
)

ax1.text(
    0,
    -label_offset,
    0,
    "-S2",
    fontsize=8
)

ax1.text(
    0,
    0,
    -label_offset,
    "-S3",
    fontsize=8
)



# make real sphere scaling

ax1.set_box_aspect([1,1,1])


# remove all bounding box junk

ax1.set_axis_off()


# allow rotation
ax1.view_init(
    elev=25,
    azim=45
)



# ---- S1 = 0 plane ellipticity plot ----

ax2 = fig.add_subplot(122)

ell_line, = ax2.plot(
    [],
    []
)

ax2.set_title("Ellipticity trajectory (S1 = 0 plane)")
ax2.set_xlabel("S2")
ax2.set_ylabel("S3")

ax2.set_xlim([-1,1])
ax2.set_ylim([-1,1])

ax2.grid(True)

# draw reference circle
circle = plt.Circle(
    (0,0),
    1,
    fill=False,
    alpha=0.3
)

ax2.add_patch(circle)



# =========================
# UPDATE FUNCTION
# =========================

start_time = time.time()


def update(frame):

    if not running:
        return


    # trigger measurement
    c.measure()


    # wait for instrument update
    time.sleep(0.06)


    data = c.get_measured()


    theta = data["theta"]
    eta = data["eta"]
    dop = data["dop"]


    t = time.time() - start_time


    times.append(t)
    thetas.append(theta)
    etas.append(eta)
    dops.append(dop)


    # calculate Stokes coordinates

    S1,S2,S3 = poincare_coordinates(
        theta,
        eta,
        dop
    )

    S1_history.append(S1)
    S2_history.append(S2)
    S3_history.append(S3)


    # limit memory
    if len(times) > MAX_POINTS:

        times.pop(0)
        thetas.pop(0)
        etas.pop(0)
        dops.pop(0)

        S1_history.pop(0)
        S2_history.pop(0)
        S3_history.pop(0)



    # Poincare update
    # push point slightly outside sphere surface so it stays visible

    r = 1.1

    X = r*S1
    Y = r*S2
    Z = r*S3


    # point slightly above sphere surface
    r = 1.03

    point._offsets3d = (
        np.array([r*S1]),
        np.array([r*S2]),
        np.array([r*S3])
    )


    # trajectory trace slightly above sphere
    trace._verts3d = (
        r*np.array(S1_history),
        r*np.array(S2_history),
        r*np.array(S3_history)
    )


    # S1=0 ellipticity plane update

    ell_line.set_data(
        S2_history,
        S3_history
    )


    return point, trace, ell_line



# =========================
# START
# =========================


interval = int(1000/UPDATE_HZ)


print(
    f"Starting live plot at {UPDATE_HZ} Hz"
)

ani = FuncAnimation(
    fig,
    update,
    interval=interval,
    cache_frame_data=False
)


plt.tight_layout()

plt.show()