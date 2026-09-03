"""core.hologram 단위 테스트."""
from __future__ import annotations

import math
import unittest

import core.hologram as holo


class TestGrid(unittest.TestCase):
    def test_grid_shape(self):
        rows = holo.compute_grid(lat=8, lon=14)
        self.assertEqual(len(rows), 9)      # lat+1
        self.assertEqual(len(rows[0]), 15)  # lon+1

    def test_unit_sphere_radius(self):
        rows = holo.compute_grid(lat=6, lon=10, rot_y=0.7, rot_x=0.3)
        flat = [p for row in rows for p in row]
        self.assertTrue(holo.is_within_radius(flat, 1.0))

    def test_rotation_is_periodic(self):
        a = holo.compute_grid(lat=4, lon=6, rot_y=0.0)
        b = holo.compute_grid(lat=4, lon=6, rot_y=2 * math.pi)
        for ra, rb in zip(a, b):
            for (x1, y1, z1), (x2, y2, z2) in zip(ra, rb):
                self.assertAlmostEqual(x1, x2, places=6)
                self.assertAlmostEqual(y1, y2, places=6)
                self.assertAlmostEqual(z1, z2, places=6)

    def test_rotation_changes_points(self):
        a = holo.compute_grid(lat=3, lon=5, rot_y=0.0)
        b = holo.compute_grid(lat=3, lon=5, rot_y=1.0)
        flat_a = [p for row in a for p in row]
        flat_b = [p for row in b for p in row]
        self.assertNotEqual(flat_a, flat_b)


class TestProjection(unittest.TestCase):
    def test_projection_within_circle(self):
        rows = holo.compute_grid(lat=6, lon=10, rot_y=0.4, rot_x=0.2)
        flat = [p for row in rows for p in row]
        pts = holo.project(flat, cx=100.0, cy=100.0, radius=50.0)
        for x, y in pts:
            self.assertLessEqual(math.hypot(x - 100, y - 100), 50.0 + 1e-6)

    def test_projection_count(self):
        rows = holo.compute_grid(lat=4, lon=6)
        flat = [p for row in rows for p in row]
        self.assertEqual(len(holo.project(flat, 0, 0, 10)), len(flat))


class TestEffects(unittest.TestCase):
    def test_scanline_range(self):
        for tick in range(100):
            for y in (-1.0, -0.5, 0.0, 0.5, 1.0):
                v = holo.scanline_phase(tick, y)
                self.assertTrue(0.0 <= v <= 1.0)

    def test_flicker_range(self):
        for tick in range(200):
            v = holo.flicker(tick)
            self.assertTrue(0.85 <= v <= 1.0)

    def test_head_profile_ellipse(self):
        for i in range(24):
            x, y = holo.head_profile(i / 24)
            self.assertTrue(-1.0 <= x <= 1.0)
            self.assertTrue(-0.7 <= y <= 0.7)


if __name__ == "__main__":
    unittest.main()
