"""PyVista Poincare-sphere view driven by readings owned by the lock loop.

This intentionally contains no YAQC/PAX client. The PID loop supplies each
reading, preventing two applications from competing for PAX acquisition.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable

import numpy as np

from ..control import SphereAngles
from ..hardware.pax_interface import PAXReading


class PIDPoincareView:
    """Interactive Poincare view adapted from PAX1000-PS_live_plot-PyVista.py."""

    def __init__(
        self,
        get_target: Callable[[], SphereAngles],
        set_target: Callable[[float, float], None],
        refresh_period_s: float = 0.10,
    ) -> None:
        try:
            import pyvista as pv
            from pyvistaqt import BackgroundPlotter
        except ImportError as exc:  # pragma: no cover - GUI dependency
            raise RuntimeError("pid-live requires pyvista and pyvistaqt") from exc

        self.pv = pv
        self.get_target = get_target
        self.set_target = set_target
        self.running = True
        self.refresh_period_s = refresh_period_s
        self.last_render_s = -math.inf
        self.max_points = 180
        self.history: list[np.ndarray] = []
        self.plotter = BackgroundPlotter(window_size=(900, 900))
        self.plotter.set_background("#2F2F2F")
        self._build_scene()
        self._add_controls()

    def _build_scene(self) -> None:
        pv = self.pv
        sphere = pv.Sphere(radius=1, theta_resolution=120, phi_resolution=120)
        self.plotter.add_mesh(sphere, color="#d0d0d0", smooth_shading=True, opacity=1.0)
        self.plotter.add_mesh(sphere, style="wireframe", color="#d0d0d0", opacity=0.12, line_width=1)

        gap = np.deg2rad(12)
        for axis in ("S1", "S2", "S3"):
            for start, end in zip((0, np.pi / 2, np.pi, 3 * np.pi / 2), (np.pi / 2, np.pi, 3 * np.pi / 2, 2 * np.pi)):
                self._add_arc(axis, start + gap, end - gap)

        radius = 1.03
        self._add_label("H", (radius, 0, 0), (("y", 90),))
        self._add_label("V", (-radius, 0, 0), (("y", -90),))
        self._add_label("D", (0, radius, 0), (("x", -90),))
        self._add_label("A", (0, -radius, 0), (("x", 90),))
        self._add_label("L", (0, 0, radius))
        self._add_label("R", (0, 0, -radius), (("y", 180),))

        self.point_actor = self.plotter.add_mesh(
            pv.PolyData(np.array([[0.0, 0.0, 0.0]])).glyph(geom=pv.Sphere(radius=0.035)),
            color="red", smooth_shading=True,
        )
        self.target_actor = self.plotter.add_mesh(
            pv.PolyData(np.array([[0.0, 0.0, 0.0]])).glyph(geom=pv.Sphere(radius=0.042)),
            color="#00d9ff", smooth_shading=True,
        )
        self.trace_actor = self.plotter.add_mesh(pv.PolyData(np.array([[0.0, 0.0, 0.0]])), color="red", line_width=4)
        self.plotter.add_text(
            "PID live Poincare sphere\ncyan: target | red: measured\nsliders or J/L (u), I/K (v), R reset, Q stop",
            position="upper_left", font_size=9, color="white", name="pid_status",
        )

    def _add_arc(self, axis: str, start: float, end: float) -> None:
        t = np.linspace(start, end, 80)
        if axis == "S1":
            points = np.column_stack((np.zeros_like(t), np.cos(t), np.sin(t)))
        elif axis == "S2":
            points = np.column_stack((np.cos(t), np.zeros_like(t), np.sin(t)))
        else:
            points = np.column_stack((np.cos(t), np.sin(t), np.zeros_like(t)))
        self.plotter.add_mesh(self.pv.lines_from_points(points), color="black", line_width=3)

    def _add_label(self, text: str, position: tuple[float, float, float], rotations: tuple[tuple[str, int], ...] = ()) -> None:
        label = self.pv.Text3D(text, depth=0.01)
        label.translate(-np.array(label.center), inplace=True)
        label.scale(0.08, inplace=True)
        for axis, angle in rotations:
            getattr(label, f"rotate_{axis}")(angle, inplace=True)
        label.translate(position, inplace=True)
        self.plotter.add_mesh(label, color="black", smooth_shading=True)

    def _add_controls(self) -> None:
        target = self.get_target()
        self.plotter.add_slider_widget(
            self._set_u, rng=(-math.pi, math.pi), value=target.u, title="Target u (rad)",
            pointa=(0.03, 0.08), pointb=(0.40, 0.08), color="cyan", interaction_event="always",
        )
        self.plotter.add_slider_widget(
            self._set_v, rng=(0.0, math.pi), value=target.v, title="Target v (rad)",
            pointa=(0.03, 0.03), pointb=(0.40, 0.03), color="cyan", interaction_event="always",
        )
        self.plotter.add_key_event("j", lambda: self._adjust_target(-0.1, 0.0))
        self.plotter.add_key_event("l", lambda: self._adjust_target(+0.1, 0.0))
        self.plotter.add_key_event("i", lambda: self._adjust_target(0.0, +0.05))
        self.plotter.add_key_event("k", lambda: self._adjust_target(0.0, -0.05))
        self.plotter.add_key_event("r", self.reset_trace)
        self.plotter.add_key_event("R", self.reset_trace)
        self.plotter.add_key_event("q", self.stop)
        self.plotter.add_key_event("Q", self.stop)

    def _set_u(self, value: float) -> None:
        target = self.get_target()
        self.set_target(float(value), target.v)

    def _set_v(self, value: float) -> None:
        target = self.get_target()
        self.set_target(target.u, float(value))

    def _adjust_target(self, delta_u: float, delta_v: float) -> None:
        target = self.get_target()
        self.set_target(target.u + delta_u, float(np.clip(target.v + delta_v, 0.0, math.pi)))

    @staticmethod
    def _sphere_point(state: SphereAngles, radius: float = 1.03) -> np.ndarray:
        return radius * np.array([[math.cos(state.v), math.sin(state.v) * math.cos(state.u), math.sin(state.v) * math.sin(state.u)]])

    def update(self, reading: PAXReading, state: SphereAngles) -> None:
        """Render one reading acquired by the PID loop; never acquire PAX here."""
        if not self.running:
            return
        try:
            self.plotter.app.processEvents()
        except Exception:
            pass
        now = time.monotonic()
        if now - self.last_render_s < self.refresh_period_s:
            return
        self.last_render_s = now
        current = np.array([[reading.s1, reading.s2, reading.s3]], dtype=float)
        self.history.append(current[0])
        if len(self.history) > self.max_points:
            self.history.pop(0)

        self.point_actor.mapper.SetInputData(
            self.pv.PolyData(current * 1.03).glyph(geom=self.pv.Sphere(radius=0.035))
        )
        self.target_actor.mapper.SetInputData(
            self.pv.PolyData(self._sphere_point(self.get_target())).glyph(geom=self.pv.Sphere(radius=0.042))
        )
        if len(self.history) > 2:
            raw_path = np.asarray(self.history)
            raw_path /= np.linalg.norm(raw_path, axis=1)[:, None]
            smooth = []
            for first, second in zip(raw_path[:-1], raw_path[1:]):
                for fraction in np.linspace(0.0, 1.0, 10):
                    point = (1.0 - fraction) * first + fraction * second
                    smooth.append(point / np.linalg.norm(point))
            trace_tube = self.pv.lines_from_points(np.asarray(smooth) * 1.002).tube(radius=0.006)
            self.trace_actor.mapper.SetInputData(trace_tube)

        target = self.get_target()
        self.plotter.add_text(
            f"PID live Poincare sphere\ncyan: target | red: measured\n"
            f"target u={target.u:+.3f}, v={target.v:.3f}\n"
            f"measured u={state.u:+.3f}, v={state.v:.3f}, raw DOP={reading.dop:.3f}\n"
            "sliders or J/L (u), I/K (v), R reset, Q stop",
            position="upper_left", font_size=9, color="white", name="pid_status",
        )
        self.plotter.render()

    def reset_trace(self) -> None:
        self.history.clear()
        self.trace_actor.mapper.SetInputData(self.pv.PolyData(np.array([[0.0, 0.0, 0.0]])))
        self.plotter.render()

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        self.running = False
        try:
            self.plotter.close()
        except Exception:
            pass
