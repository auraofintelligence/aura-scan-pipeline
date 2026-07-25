"""Asset dimensioning, plane fitting and section cutting on known shapes."""

from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

from aura_scan.config import load_config
from aura_scan.ops.asset import analyse_asset
from aura_scan.ops.plane import analyse_projection_surface
from aura_scan.ops.section import SectionSpec, cut_sections
from aura_scan.util import trimesh_to_o3d

from synthetic import (
    TABLE_HEIGHT,
    TABLE_LENGTH,
    make_picnic_table_mesh,
    make_wall_cloud,
    wall_plane_normal,
)


def test_analyse_asset_picnic_table() -> None:
    cfg = load_config("asset")
    report = analyse_asset(make_picnic_table_mesh(), cfg)

    dims = report.dims_m
    assert abs(dims["length"] - TABLE_LENGTH) <= 0.05 * TABLE_LENGTH
    assert abs(dims["height"] - TABLE_HEIGHT) <= 0.05 * TABLE_HEIGHT
    assert dims["length"] >= dims["width"]

    heights = [float(h) for h in report.key_heights_m]
    assert any(abs(h - 0.45) <= 0.06 for h in heights), (
        f"no key height near the 0.45 m seats: {heights}")
    assert any(abs(h - 0.78) <= 0.06 for h in heights), (
        f"no key height near the 0.78 m tabletop: {heights}")

    json.dumps(report.stats)  # stats must be JSON-serialisable


def test_analyse_projection_surface_wall() -> None:
    noise = 0.003
    cfg = load_config("projection_surface")
    report = analyse_projection_surface(make_wall_cloud(noise=noise), cfg)

    assert report.rmse_m < 3 * noise
    assert report.inlier_ratio > 0.8
    assert abs(report.usable_area_m2 - 6.0) <= 0.2 * 6.0  # 3 m x 2 m wall
    assert report.suitable is True

    fitted = np.asarray(report.normal, dtype=float)
    fitted = fitted / np.linalg.norm(fitted)
    expected = wall_plane_normal()
    # Sign of the fitted normal is arbitrary.
    assert abs(float(np.dot(fitted, expected))) > 0.99


def _ring_area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])))


def test_cut_sections_unit_box() -> None:
    cfg = load_config("general")
    box = trimesh_to_o3d(trimesh.creation.box(extents=(1.0, 1.0, 1.0)))
    specs = [
        SectionSpec(name="plan", origin=np.zeros(3), normal=np.array([0.0, 0.0, 1.0])),
        SectionSpec(name="cross", origin=np.zeros(3), normal=np.array([1.0, 0.0, 0.0])),
    ]

    results = cut_sections(box, specs, cfg)

    assert len(results) == 2
    for result in results:
        assert result.polylines, f"section {result.spec.name} produced no polylines"
        poly = np.asarray(max(result.polylines, key=len))
        assert poly.ndim == 2 and poly.shape[1] == 2
        assert np.allclose(poly[0], poly[-1], atol=1e-6), "polyline should close"
        # The cut of a unit cube through its centre is a unit square:
        # area and perimeter are invariant to the in-plane 2D basis chosen.
        assert _ring_area(poly) == pytest.approx(1.0, abs=0.01)
        perimeter = float(np.sum(np.linalg.norm(np.diff(poly, axis=0), axis=1)))
        assert perimeter == pytest.approx(4.0, abs=0.04)
        for extent in result.extent_2d:
            assert 0.99 <= float(extent) <= 1.45  # 1.0 axis-aligned, up to sqrt(2) rotated
