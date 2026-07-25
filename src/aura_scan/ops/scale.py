"""Scale verification: solve a uniform factor from tape-measured constraints.

ARKit output is already metric, so this layer verifies rather than invents
scale. Each constraint pairs two picked points with a real tape-measured
distance; the least-squares factor and per-constraint errors feed the
metadata scale block so an engineering reader can judge the scan at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..util import PipelineError, get_logger

log = get_logger(__name__)


@dataclass
class DistanceConstraint:
    a: np.ndarray   # (3,)
    b: np.ndarray   # (3,)
    distance_m: float
    note: str = ""


@dataclass
class ScaleResult:
    factor: float
    rmse_before_m: float
    rmse_after_m: float
    max_error_after_m: float
    constraints: list[dict]   # {note, distance_m, measured_m, error_before_m, error_after_m}


def solve_scale(constraints: list[DistanceConstraint]) -> ScaleResult:
    """Least-squares uniform scale factor: sum(real * measured) / sum(measured^2)."""
    if not constraints:
        raise PipelineError(
            "No distance constraints to solve. Pick points with 'aura-scan pick' "
            "and add control.distance_constraints to the manifest first."
        )

    measured = np.empty(len(constraints), dtype=float)
    real = np.empty(len(constraints), dtype=float)
    for i, c in enumerate(constraints):
        dist = float(np.linalg.norm(
            np.asarray(c.a, dtype=float) - np.asarray(c.b, dtype=float)
        ))
        if dist < 1e-6:
            label = c.note or f"constraint {i + 1}"
            raise PipelineError(
                f"Distance constraint '{label}' spans {dist:.2e} m in the scan; "
                "its two picked points are effectively the same point. "
                "Re-pick them with 'aura-scan pick'."
            )
        measured[i] = dist
        real[i] = float(c.distance_m)

    factor = float((real * measured).sum() / (measured ** 2).sum())
    error_before = np.abs(measured - real)
    error_after = np.abs(factor * measured - real)

    per_constraint = [
        {
            "note": c.note,
            "distance_m": float(r),
            "measured_m": float(m),
            "error_before_m": float(eb),
            "error_after_m": float(ea),
        }
        for c, r, m, eb, ea in zip(constraints, real, measured, error_before, error_after)
    ]
    result = ScaleResult(
        factor=factor,
        rmse_before_m=float(np.sqrt((error_before ** 2).mean())),
        rmse_after_m=float(np.sqrt((error_after ** 2).mean())),
        max_error_after_m=float(error_after.max()),
        constraints=per_constraint,
    )
    log.info(
        "Scale solve over %d constraint(s): factor %.5f, RMSE %.4f m -> %.4f m",
        len(constraints), result.factor, result.rmse_before_m, result.rmse_after_m,
    )
    return result


def apply_scale(geometry, factor: float) -> None:
    """Uniformly scale a point cloud or mesh in place about the origin."""
    factor = float(factor)
    if not np.isfinite(factor) or factor <= 0:
        raise PipelineError(
            f"Refusing to apply scale factor {factor}; it must be a positive "
            "finite number. Check the distance constraints in the manifest."
        )
    geometry.scale(factor, center=np.zeros(3))


def verification_block(result: ScaleResult | None, applied: bool) -> dict:
    """The metadata 'scale' block per DESIGN.md schema 2.0.

    rmse_m and max_error_m report the residuals of the solved factor; when no
    constraints exist the scan rides on ARKit's metric scale, unverified.
    """
    if result is None:
        return {
            "method": "arkit",
            "factor_applied": 1.0,
            "verified": False,
            "constraints": [],
            "rmse_m": None,
            "max_error_m": None,
        }
    return {
        "method": "control_points",
        "factor_applied": float(result.factor) if applied else 1.0,
        "verified": True,
        "constraints": list(result.constraints),
        "rmse_m": float(result.rmse_after_m),
        "max_error_m": float(result.max_error_after_m),
    }
