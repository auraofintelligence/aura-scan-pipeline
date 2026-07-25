"""Surface reconstruction and the LOD ladder on a known box."""

from __future__ import annotations

import json

import numpy as np
import open3d as o3d

from aura_scan.config import load_config
from aura_scan.ops.meshing import pointcloud_to_mesh
from aura_scan.ops.simplify import make_lods

from synthetic import make_box_cloud

BOX_EXTENTS = (1.0, 0.7, 0.5)
LOD1_CAP = 2000


def _reconstruct(cfg: dict) -> o3d.geometry.TriangleMesh:
    pc = make_box_cloud(BOX_EXTENTS, n=20000, noise=0.002, seed=7)
    return pointcloud_to_mesh(pc, cfg)


def test_pointcloud_to_mesh_recovers_box_extents() -> None:
    cfg = load_config("general", overrides={"mesh": {"poisson": {"depth": 8}}})
    mesh = _reconstruct(cfg)

    assert len(mesh.triangles) > 0
    assert len(mesh.vertices) > 0
    extent = np.asarray(mesh.get_axis_aligned_bounding_box().get_extent())
    for got, want in zip(extent, BOX_EXTENTS):
        assert abs(got - want) <= 0.08 * want, (
            f"reconstructed extent {got:.3f} vs expected {want:.3f}")


def test_make_lods_caps_lod1_and_closes_footprint() -> None:
    cfg = load_config("general", overrides={
        "mesh": {"poisson": {"depth": 8}},
        "lods": {"lod1_max_triangles": LOD1_CAP},
    })
    mesh = _reconstruct(cfg)
    assert len(mesh.triangles) > LOD1_CAP  # decimation must genuinely happen

    lods = make_lods(mesh, cfg)

    assert len(lods.lod1.triangles) <= LOD1_CAP * 1.05
    assert len(lods.lod1.triangles) < len(mesh.triangles)
    assert len(lods.lod2.triangles) <= 400000
    assert len(lods.lod0_box.triangles) > 0

    footprint = np.asarray(lods.footprint_2d)
    assert footprint.ndim == 2 and footprint.shape[1] == 2
    assert len(footprint) >= 4
    assert np.allclose(footprint[0], footprint[-1], atol=1e-8), "footprint must close"
    spans = footprint.max(axis=0) - footprint.min(axis=0)
    assert abs(spans[0] - BOX_EXTENTS[0]) <= 0.15 * BOX_EXTENTS[0]
    assert abs(spans[1] - BOX_EXTENTS[1]) <= 0.15 * BOX_EXTENTS[1]

    json.dumps(lods.stats)  # stats must be JSON-serialisable
