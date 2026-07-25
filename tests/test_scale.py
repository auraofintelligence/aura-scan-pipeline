"""Scale solver and rigid registration maths against known ground truth."""

from __future__ import annotations

import json

import numpy as np
import pytest

from aura_scan.ops.register import umeyama
from aura_scan.ops.scale import DistanceConstraint, solve_scale

# The "scan" frame is 3 percent small; the solver should recover 1/0.97.
TRUE_SCALE = 0.97


def _constraints_from_true_pairs(
    pairs: list[tuple[np.ndarray, np.ndarray]],
) -> list[DistanceConstraint]:
    """Points measured in a frame scaled by TRUE_SCALE, distances at true scale."""
    constraints = []
    for p, q in pairs:
        p = np.asarray(p, dtype=float)
        q = np.asarray(q, dtype=float)
        constraints.append(DistanceConstraint(
            a=p * TRUE_SCALE,
            b=q * TRUE_SCALE,
            distance_m=float(np.linalg.norm(p - q)),
            note="synthetic tape measure",
        ))
    return constraints


def test_solve_scale_recovers_known_factor() -> None:
    rng = np.random.default_rng(3)
    pairs = [(rng.uniform(-2, 2, 3), rng.uniform(-2, 2, 3)) for _ in range(4)]
    result = solve_scale(_constraints_from_true_pairs(pairs))

    assert result.factor == pytest.approx(1.0 / TRUE_SCALE, abs=1e-6)
    assert result.rmse_before_m > 0.0
    assert result.rmse_after_m < 1e-9
    assert result.max_error_after_m < 1e-9

    assert len(result.constraints) == 4
    for entry in result.constraints:
        assert {"note", "distance_m", "measured_m",
                "error_before_m", "error_after_m"} <= set(entry)
        assert entry["measured_m"] == pytest.approx(
            TRUE_SCALE * entry["distance_m"], rel=1e-9)
        assert entry["error_after_m"] == pytest.approx(0.0, abs=1e-9)
    json.dumps(result.constraints)  # must be JSON-serialisable


def test_umeyama_recovers_rigid_transform() -> None:
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(5)
    src = rng.uniform(-2, 2, (60, 3))
    rot = Rotation.from_euler("xyz", [20, -35, 70], degrees=True).as_matrix()
    t = np.array([0.4, -1.2, 2.5])
    dst = src @ rot.T + t

    transform, scale, residuals = umeyama(src, dst, with_scale=False)

    assert scale == pytest.approx(1.0, abs=1e-9)
    assert np.allclose(transform[:3, :3], rot, atol=1e-8)
    assert np.allclose(transform[:3, 3], t, atol=1e-8)
    assert np.allclose(transform[3], [0.0, 0.0, 0.0, 1.0], atol=1e-12)

    moved = src @ transform[:3, :3].T + transform[:3, 3]
    assert np.allclose(moved, dst, atol=1e-8)
    assert float(np.max(np.abs(residuals))) < 1e-8


def test_umeyama_recovers_similarity_scale() -> None:
    from scipy.spatial.transform import Rotation

    rng = np.random.default_rng(9)
    src = rng.uniform(-1, 1, (40, 3))
    rot = Rotation.from_euler("zyx", [15, 40, -25], degrees=True).as_matrix()
    t = np.array([-0.3, 0.8, 0.1])
    dst = 0.5 * (src @ rot.T) + t

    transform, scale, residuals = umeyama(src, dst, with_scale=True)

    assert scale == pytest.approx(0.5, abs=1e-9)
    moved = src @ transform[:3, :3].T + transform[:3, 3]
    assert np.allclose(moved, dst, atol=1e-8)
    assert float(np.max(np.abs(residuals))) < 1e-8
