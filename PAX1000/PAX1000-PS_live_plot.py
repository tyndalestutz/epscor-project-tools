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
ellipticities = []


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
    """
    Convert PAX angles to Poincare sphere coordinates.

    theta = azimuth
    eta   = ellipticity angle

    Uses:
        S1 = cos(2eta)cos(2theta)
        S2 = cos(2eta)sin(2theta)
        S3 = sin(2eta)

    """

    S1 = np.cos(2*eta)*np.cos(2*theta)
    S2 = np.cos(2*eta)*np.sin(2*theta)
    S3 = np.sin(2*eta)

    return dop*S1, dop*S2, dop*S3



# =========================
# FIGURE
# =========================

fig = plt.figure(figsize=(12,6))


# ---- Poincare sphere ----

ax1 = fig.add_subplot(
    121,
    projection="3d"
)

ax1.set_title("Poincaré Sphere")

u = np.linspace(0,2*np.pi,40)
v = np.linspace(0,np.pi,20)

x = np.outer(np.cos(u),np.sin(v))
y = np.outer(np.sin(u),np.sin(v))
z = np.outer(np.ones(np.size(u)),np.cos(v))

ax1.plot_wireframe(
    x,y,z,
    alpha=0.15
)

ax1.set_xlim([-1,1])
ax1.set_ylim([-1,1])
ax1.set_zlim([-1,1])


point, = ax1.plot(
    [],
    [],
    [],
    "o",
    markersize=8
)


# ---- Ellipticity ----

ax2 = fig.add_subplot(122)

ell_line, = ax2.plot(
    [],
    []
)

ax2.set_title("Ellipticity")
ax2.set_xlabel("Time (s)")
ax2.set_ylabel("eta (rad)")

ax2.grid(True)



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
    ellipticities.append(eta)


    # limit memory
    if len(times) > MAX_POINTS:

        times.pop(0)
        thetas.pop(0)
        etas.pop(0)
        dops.pop(0)
        ellipticities.pop(0)


    # Poincare update

    X,Y,Z = poincare_coordinates(
        theta,
        eta,
        dop
    )


    point.set_data(
        [X],
        [Y]
    )

    point.set_3d_properties(
        [Z]
    )


    # ellipticity update

    ell_line.set_data(
        times,
        ellipticities
    )

    ax2.relim()
    ax2.autoscale_view()


    return point, ell_line



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
