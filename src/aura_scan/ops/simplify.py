"""LOD ladder: archival mesh, web mesh, oriented box and 2D footprint from one capture."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger, o3d_mesh_to_trimesh, trimesh_to_o3d

log = get_logger(__name__)


@dataclass
class LodSet:
    lod0_box: o3d.geometry.TriangleMesh
    footprint_2d: np.ndarray
    lod1: o3d.geometry.TriangleMesh
    lod2: o3d.geometry.TriangleMesh
    stats: dict


def _decimate(mesh: o3d.geometry.TriangleMesh, target: int) -> o3d.geometry.TriangleMesh:
    if len(mesh.triangles) <= target:
        return mesh
    out = mesh.simplify_quadric_decimation(target_number_of_triangles=target)
    out.remove_unreferenced_vertices()
    return out


def _oriented_box(mesh: o3d.geometry.TriangleMesh) -> o3d.geometry.TriangleMesh:
    """Tight oriented bounding box as a closed box mesh in the world frame."""
    import trimesh

    tm = o3d_mesh_to_trimesh(mesh)
    # to_origin maps the mesh into a frame where the box is axis aligned and
    # centred at the origin, so its inverse places the box back in the world.
    to_origin, extents = trimesh.bounds.oriented_bounds(tm)
    box = trimesh.creation.box(extents=extents)
    box.apply_transform(np.linalg.inv(to_origin))
    return trimesh_to_o3d(box)


def _footprint(lod1: o3d.geometry.TriangleMesh, cfg: dict) -> np.ndarray:
    """Closed XY outline of the LOD1 vertices: concave hull, convex fallback, simplified."""
    import shapely
    from shapely.geometry import MultiPoint

    vertices = np.asarray(lod1.vertices)
    if vertices.shape[0] < 3:
        raise PipelineError(
            "Not enough vertices for a footprint outline; the simplified mesh is degenerate."
        )
    points = MultiPoint(vertices[:, :2])
    hull = None
    try:
        hull = shapely.concave_hull(points, ratio=0.3)
    except Exception as exc:
        log.warning("Concave hull failed (%s); falling back to convex hull", exc)
    if hull is None or hull.is_empty or hull.geom_type != "Polygon":
        hull = points.convex_hull
    if hull.geom_type != "Polygon":
        raise PipelineError(
            "Footprint outline is degenerate: the XY projection collapses to a line or point. "
            "Check that the scan is oriented Z up before running the pipeline."
        )
    tolerance = float(cfg_get(cfg, "lods.footprint_simplify_m", 0.02))
    if tolerance > 0.0:
        simplified = hull.simplify(tolerance)
        if not simplified.is_empty and simplified.geom_type == "Polygon":
            hull = simplified
    ring = np.asarray(hull.exterior.coords, dtype=np.float64)
    if not np.allclose(ring[0], ring[-1]):
        ring = np.vstack([ring, ring[0]])
    return ring


def make_lods(mesh: o3d.geometry.TriangleMesh, cfg: dict) -> LodSet:
    """Build the four-tier LOD ladder from the repaired full-detail mesh."""
    if len(mesh.triangles) == 0:
        raise PipelineError(
            "Cannot build LODs from an empty mesh; surface reconstruction produced no triangles."
        )
    input_triangles = int(len(mesh.triangles))
    lod2_max = int(cfg_get(cfg, "lods.lod2_max_triangles", 400_000))
    lod1_max = int(cfg_get(cfg, "lods.lod1_max_triangles", 15_000))

    lod2 = _decimate(mesh, lod2_max)
    lod2.compute_vertex_normals()
    lod1 = _decimate(lod2, lod1_max)
    lod1.compute_vertex_normals()
    lod0_box = _oriented_box(mesh)
    footprint_2d = _footprint(lod1, cfg)

    stats = {
        "input_triangles": input_triangles,
        "lod2_triangles": int(len(lod2.triangles)),
        "lod1_triangles": int(len(lod1.triangles)),
        "lod0_triangles": int(len(lod0_box.triangles)),
        "footprint_vertices": int(footprint_2d.shape[0]),
    }
    log.info(
        "LOD ladder: lod2 %d tris, lod1 %d tris, footprint %d points",
        stats["lod2_triangles"], stats["lod1_triangles"], stats["footprint_vertices"],
    )
    return LodSet(lod0_box=lod0_box, footprint_2d=footprint_2d, lod1=lod1, lod2=lod2, stats=stats)
