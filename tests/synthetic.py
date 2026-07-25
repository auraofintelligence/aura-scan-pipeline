"""Synthetic geometry fixtures with known dimensions.

Everything here is deterministic (fixed seeds, analytic shapes) so tests can
assert real numbers rather than smoke-check. Units are metres, Z up, matching
the pipeline's internal frame.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh

from aura_scan.project import ScanProject
from aura_scan.util import trimesh_to_o3d

# Picnic table dimensions shared with the analysis tests.
TABLE_LENGTH = 1.8
TABLE_TOP_WIDTH = 0.85
TABLE_TOP_THICKNESS = 0.04
TABLE_TOP_UNDERSIDE = 0.78  # tabletop spans 0.78 to 0.82, so overall height is 0.82
TABLE_HEIGHT = TABLE_TOP_UNDERSIDE + TABLE_TOP_THICKNESS
BENCH_TOP = 0.45
BENCH_WIDTH = 0.25
BENCH_THICKNESS = 0.04
BENCH_OFFSET_Y = 0.55


def _sample_surface(tm: trimesh.Trimesh, n: int, rng: np.random.Generator) -> np.ndarray:
    """Area-weighted surface sampling with our own generator, for determinism."""
    faces = rng.choice(len(tm.faces), size=n, p=tm.area_faces / tm.area)
    tri = tm.triangles[faces]
    # Uniform barycentric coordinates over each triangle.
    r1 = np.sqrt(rng.random(n))
    r2 = rng.random(n)
    a = 1.0 - r1
    b = r1 * (1.0 - r2)
    c = r1 * r2
    return tri[:, 0] * a[:, None] + tri[:, 1] * b[:, None] + tri[:, 2] * c[:, None]


def make_box_cloud(extents: tuple[float, float, float], n: int = 20000,
                   noise: float = 0.002, seed: int = 7) -> o3d.geometry.PointCloud:
    """Noisy point cloud sampled from the surface of a box centred at the origin."""
    rng = np.random.default_rng(seed)
    box = trimesh.creation.box(extents=extents)
    points = _sample_surface(box, n, rng)
    points = points + rng.normal(scale=noise, size=points.shape)
    return o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))


def _box_part(extents: tuple[float, float, float],
              transform: np.ndarray | None = None,
              translate: tuple[float, float, float] | None = None) -> trimesh.Trimesh:
    part = trimesh.creation.box(extents=extents)
    if transform is not None:
        part.apply_transform(transform)
    if translate is not None:
        part.apply_translation(translate)
    return part


def make_picnic_table_mesh() -> o3d.geometry.TriangleMesh:
    """A believable A-frame-ish picnic table, 1.8 m long and 0.82 m tall.

    Tabletop 1.8 x 0.85 x 0.04 with its underside at 0.78, two bench tops
    1.8 x 0.25 with their seats at 0.45, and four legs splayed outward in Y.
    Composed from trimesh box primitives, so vertex heights cluster at the
    seat and tabletop levels the key-height analysis should find.
    """
    parts: list[trimesh.Trimesh] = []
    parts.append(_box_part(
        (TABLE_LENGTH, TABLE_TOP_WIDTH, TABLE_TOP_THICKNESS),
        translate=(0.0, 0.0, TABLE_TOP_UNDERSIDE + TABLE_TOP_THICKNESS / 2),
    ))
    for side in (-1.0, 1.0):
        parts.append(_box_part(
            (TABLE_LENGTH, BENCH_WIDTH, BENCH_THICKNESS),
            translate=(0.0, side * BENCH_OFFSET_Y, BENCH_TOP - BENCH_THICKNESS / 2),
        ))
    leg_width = 0.08
    leg_length = 0.80
    tilt = np.deg2rad(20.0)
    # Vertical span of a tilted leg, so its feet land on the ground plane.
    half_span = (leg_length * np.cos(tilt) + leg_width * np.sin(tilt)) / 2
    for end in (-1.0, 1.0):
        for side in (-1.0, 1.0):
            rot = trimesh.transformations.rotation_matrix(side * tilt, (1.0, 0.0, 0.0))
            parts.append(_box_part(
                (leg_width, leg_width, leg_length),
                transform=rot,
                translate=(end * 0.7, side * 0.28, half_span),
            ))
    return trimesh_to_o3d(trimesh.util.concatenate(parts))


def _wall_rotation(tilt_deg: float) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    return Rotation.from_euler("xz", [tilt_deg, tilt_deg], degrees=True).as_matrix()


def wall_plane_normal(tilt_deg: float = 10.0) -> np.ndarray:
    """Unit normal of the plane make_wall_cloud samples, before noise."""
    return _wall_rotation(tilt_deg) @ np.array([0.0, 1.0, 0.0])


def make_wall_cloud(width: float = 3.0, height: float = 2.0, noise: float = 0.003,
                    tilt_deg: float = 10.0, n: int = 30000) -> o3d.geometry.PointCloud:
    """Noisy planar wall cloud, width x height metres, rotated off-axis."""
    rng = np.random.default_rng(11)
    points = np.column_stack([
        rng.uniform(-width / 2, width / 2, n),
        np.zeros(n),
        rng.uniform(0.0, height, n),
    ])
    points = points + rng.normal(scale=noise, size=points.shape)
    points = points @ _wall_rotation(tilt_deg).T
    return o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))


def make_scan_project(
    tmp_path: Path,
    mode: str,
    mesh_or_cloud: o3d.geometry.TriangleMesh | o3d.geometry.PointCloud,
) -> ScanProject:
    """Create a scan project and drop the given geometry into raw/ as input.ply."""
    project = ScanProject.init(tmp_path, f"test-{mode}", mode)
    target = project.raw_dir / "input.ply"
    if isinstance(mesh_or_cloud, o3d.geometry.TriangleMesh):
        ok = o3d.io.write_triangle_mesh(str(target), mesh_or_cloud)
    elif isinstance(mesh_or_cloud, o3d.geometry.PointCloud):
        ok = o3d.io.write_point_cloud(str(target), mesh_or_cloud)
    else:
        raise TypeError(f"Unsupported geometry type: {type(mesh_or_cloud)!r}")
    if not ok:
        raise RuntimeError(f"Open3D failed to write {target}")
    return project
