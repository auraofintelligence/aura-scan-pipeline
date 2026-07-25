"""Full pipeline runs on synthetic scans, one per capture mode."""

from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest

from aura_scan.pipeline import run_scan
from aura_scan.project import ScanProject

from synthetic import make_picnic_table_mesh, make_scan_project, make_wall_cloud

# The synthetic scan frame is treated as 3 percent small, so the solver
# should recover a factor of 1/0.97 and the pipeline should rescale.
WORLD_SCALE = 0.97

EXPECTED_GEOMETRY_KEYS = ("lod1_glb", "lod2_obj", "pointcloud_ply", "dxf")


def _add_scale_constraint(project: ScanProject, mesh) -> None:
    """Pick two real mesh vertices; claim the tape distance is theirs / 0.97."""
    verts = np.asarray(mesh.vertices)
    a = verts[int(np.argmin(verts @ np.ones(3)))]
    b = verts[int(np.argmax(verts @ np.ones(3)))]
    measured = float(np.linalg.norm(a - b))
    control = project.manifest.setdefault("control", {})
    control["picked_points"] = [a.tolist(), b.tolist()]
    control["distance_constraints"] = [{
        "points": [0, 1],
        "distance_m": measured / WORLD_SCALE,
        "note": "synthetic tape measure",
    }]


def _run_and_check(project: ScanProject) -> tuple[dict, dict]:
    summary = run_scan(project)
    exports = project.exports_dir

    for name in ("metadata.json", "places-entry.json", "run-summary.json"):
        assert (exports / name).exists(), f"missing {name}"

    with open(exports / "metadata.json", "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta["schema"] == "aura-scan-metadata/2.0"
    assert meta["capture_mode"] == project.mode

    for key in EXPECTED_GEOMETRY_KEYS:
        rel = meta["geometry_files"].get(key)
        assert rel, f"metadata geometry_files missing {key}"
        assert (exports / rel).exists(), f"export file for {key} missing: {rel}"

    doc = ezdxf.readfile(str(exports / meta["geometry_files"]["dxf"]))
    assert doc.modelspace() is not None

    with open(exports / "run-summary.json", "r", encoding="utf-8") as fh:
        json.load(fh)  # summary on disk must be valid JSON
    return summary, meta


def test_end_to_end_asset_mode(tmp_path: Path) -> None:
    mesh = make_picnic_table_mesh()
    project = make_scan_project(tmp_path, "asset", mesh)
    _add_scale_constraint(project, mesh)
    project.manifest["config"] = {
        "mesh": {"poisson": {"depth": 7}},
        # The synthetic table is seven disjoint boxes; keep them all.
        "clean": {"keep_largest_cluster": False},
    }
    project.save_manifest()

    summary, meta = _run_and_check(project)

    scale = meta["scale"]
    assert scale["method"] == "control_points"
    assert scale["factor_applied"] == pytest.approx(1.0 / WORLD_SCALE, abs=1e-6)

    # The rescale must actually reach the geometry: 1.8 m becomes 1.8/0.97.
    length = summary["dimensions_m"]["length"]
    assert length == pytest.approx(1.8 / WORLD_SCALE, rel=0.05)


def test_end_to_end_projection_surface_mode(tmp_path: Path) -> None:
    cloud = make_wall_cloud()
    project = make_scan_project(tmp_path, "projection_surface", cloud)
    project.manifest["config"] = {"mesh": {"poisson": {"depth": 7}}}
    project.save_manifest()

    _summary, meta = _run_and_check(project)

    assert meta["scale"]["method"] == "arkit"  # no constraints were supplied
    projection = meta["projection"]
    assert projection["is_projection_surface"] is True
    assert projection["planarity_rmse_m"] is not None
    assert projection["planarity_rmse_m"] < 0.01
