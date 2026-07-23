#!/usr/bin/env python3

import yaqc
import numpy as np
import pyvista as pv
from pyvistaqt import BackgroundPlotter
import signal
import sys
import time


# =========================
# USER SETTINGS
# =========================

HOST = "localhost"
PORT = 38400

UPDATE_HZ = 10
MAX_POINTS = 500


# =========================
# GLOBAL STATE
# =========================

running = True

S1_history = []
S2_history = []
S3_history = []


# =========================
# CONNECT TO YAQC
# =========================

print("Connecting to PAX1000...")

c = yaqc.Client(
    host=HOST,
    port=PORT
)

print(c)
print(c.get_channel_names())


# =========================
# POLARIZATION MATH
# =========================

def poincare_coordinates(theta, eta):

    S1 = np.cos(2*eta)*np.cos(2*theta)
    S2 = np.cos(2*eta)*np.sin(2*theta)
    S3 = np.sin(2*eta)

    return S1, S2, S3



# =========================
# CLEAN EXIT
# =========================

def shutdown(sig=None, frame=None):

    global running

    print("\nStopping acquisition...")

    running = False

    try:
        plotter.close()
    except:
        pass

    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)



# =========================
# PYVISTA WINDOW
# =========================

plotter = BackgroundPlotter(
    window_size=(900,900)
)

plotter.set_background("#2F2F2F")



# =========================
# SPHERE
# =========================

sphere = pv.Sphere(
    radius=1,
    theta_resolution=120,
    phi_resolution=120
)


plotter.add_mesh(
    sphere,
    color="#d0d0d0",
    smooth_shading=True,
    opacity=1.0
)


# wire overlay

plotter.add_mesh(
    sphere,
    style="wireframe",
    color="#d0d0d0",
    opacity=0.12,
    line_width=1
)



# =========================
# AXIAL CIRCLES
# =========================

theta = np.linspace(
    0,
    2*np.pi,
    500
)


def circle_points(axis):

    if axis=="S1":

        return np.column_stack(
            (
                np.zeros_like(theta),
                np.cos(theta),
                np.sin(theta)
            )
        )


    if axis=="S2":

        return np.column_stack(
            (
                np.cos(theta),
                np.zeros_like(theta),
                np.sin(theta)
            )
        )


    if axis=="S3":

        return np.column_stack(
            (
                np.cos(theta),
                np.sin(theta),
                np.zeros_like(theta)
            )
        )


for axis in ["S1","S2","S3"]:

    pts = circle_points(axis)

    line = pv.lines_from_points(
        pts,
        close=True
    )

    plotter.add_mesh(
        line,
        color="black",
        line_width=3
    )



# =========================
# LABELS
# =========================

plotter.add_point_labels(
    np.array(
        [
            [1.15,0,0],
            [-1.15,0,0],
            [0,1.15,0],
            [0,-1.15,0],
            [0,0,1.15],
            [0,0,-1.15],
        ]
    ),
    [
        "S1",
        "-S1",
        "S2",
        "-S2",
        "S3",
        "-S3"
    ],
    font_size=18,
    point_size=0,
    shape=None
)



# =========================
# LIVE POINT
# =========================

# initialize at origin until first measurement

point_position = np.array(
    [[0,0,0]],
    dtype=float
)


point = pv.PolyData(point_position)

point_actor = plotter.add_mesh(
    point.glyph(
        geom=pv.Sphere(radius=0.035)
    ),
    color="red",
    smooth_shading=True
)



# =========================
# LIVE TRACE
# =========================

trace = pv.PolyData(
    np.array([[0,0,0]])
)


trace_actor = plotter.add_mesh(
    trace,
    color="red",
    line_width=4
)



# =========================
# UPDATE
# =========================

def update():

    if not running:
        return


    c.measure()

    time.sleep(0.06)

    data = c.get_measured()


    theta = data["theta"]
    eta = data["eta"]


    S1,S2,S3 = poincare_coordinates(
        theta,
        eta
    )


    S1_history.append(S1)
    S2_history.append(S2)
    S3_history.append(S3)


    if len(S1_history) > MAX_POINTS:

        S1_history.pop(0)
        S2_history.pop(0)
        S3_history.pop(0)



    r = 1.03


    current = np.array(
        [
            [
                r*S1,
                r*S2,
                r*S3
            ]
        ]
    )


    # update point

    new_point = pv.PolyData(current)

    point_actor.mapper.SetInputData(
        new_point.glyph(
            geom=pv.Sphere(radius=0.035)
        )
    )



    # update trace ON sphere surface

    if len(S1_history)>2:


        raw_path = np.column_stack(
            (
                S1_history,
                S2_history,
                S3_history
            )
        )


        # normalize every point to unit sphere

        raw_path = raw_path / np.linalg.norm(
            raw_path,
            axis=1
        )[:,None]



        # interpolate between measurements

        smooth_path = []


        for i in range(len(raw_path)-1):

            p1 = raw_path[i]
            p2 = raw_path[i+1]


            steps = 10


            for t in np.linspace(0,1,steps):

                p = (1-t)*p1 + t*p2

                # project back onto sphere

                p = p / np.linalg.norm(p)

                smooth_path.append(p)



        smooth_path = np.array(smooth_path)



        # tiny offset to avoid z fighting

        smooth_path *= 1.002



        trace_mesh = pv.lines_from_points(
            smooth_path
        )


        trace_tube = trace_mesh.tube(
            radius=0.006
        )


        trace_actor.mapper.SetInputData(
            trace_tube
        )


    plotter.render()


# =========================
# RESET PLOT
# =========================

def reset_plot():

    print("Resetting polarization trace")

    S1_history.clear()
    S2_history.clear()
    S3_history.clear()


    # move point back to center

    empty_point = pv.PolyData(
        np.array([[0.0,0.0,0.0]])
    )


    point_actor.mapper.SetInputData(
        empty_point.glyph(
            geom=pv.Sphere(radius=0.035)
        )
    )


    # clear trace

    empty_trace = pv.PolyData(
        np.array([[0.0,0.0,0.0]])
    )


    trace_actor.mapper.SetInputData(
        empty_trace
    )


    plotter.render()


# =========================
# START TIMER
# =========================

plotter.add_callback(
    update,
    interval=int(1000/UPDATE_HZ)
)


print(
    f"Starting Poincare live plot at {UPDATE_HZ} Hz"
)


# keyboard shortcut

plotter.add_key_event(
    "r",
    reset_plot
)

plotter.add_key_event(
    "R",
    reset_plot
)

# keep python alive forever

plotter.app.exec()