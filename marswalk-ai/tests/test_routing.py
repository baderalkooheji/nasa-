"""Route smoothing: straighter and shorter, never steeper, never through blocked cells."""
import numpy as np

from marswalk_ai.services.routing import smooth_path


def test_staircase_becomes_straight_on_flat_ground():
    slope = np.zeros((20, 20))
    stair = [(0, 0)] + [(i // 2 + i % 2, i // 2) for i in range(1, 20)]          # zig-zag down-right
    cells, pos = smooth_path(stair, slope, 15)
    assert cells[0] == stair[0] and cells[-1] == stair[-1]
    assert len(pos) == len(cells)
    d_before = sum(np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(stair, stair[1:]))
    d_after = sum(np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(pos, pos[1:]))
    assert d_after < d_before


def test_smoothing_never_crosses_steep_or_missing_cells():
    slope = np.zeros((30, 30))
    slope[5:25, 15] = 40.0                       # a wall the grid path goes around
    slope[2, 2] = np.nan
    path = [(0, 0)] + [(r, 0) for r in range(1, 28)] + [(27, c) for c in range(1, 30)]
    cells, _ = smooth_path(path, slope, 15)
    vals = np.array([slope[r, c] for r, c in cells])
    assert np.all(np.isfinite(vals)) and vals.max() <= 15
    for (r1, c1), (r2, c2) in zip(cells, cells[1:]):                 # still an 8-connected chain of cells
        assert max(abs(r1 - r2), abs(c1 - c2)) <= 1


def test_smoothing_does_not_make_a_gentle_route_steeper():
    slope = np.full((15, 15), 2.0)
    slope[1:14, 1:14] = 12.0                     # steeper (but legal) interior; the grid path hugs the gentle edge
    path = [(0, c) for c in range(15)] + [(r, 14) for r in range(1, 15)]
    cells, _ = smooth_path(path, slope, 15)
    assert max(slope[r, c] for r, c in cells[1:-1]) <= 2.0 + 0.25
