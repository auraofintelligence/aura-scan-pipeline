"""Surface reconstruction and mesh repair: normals, Poisson, ball pivoting, cleanup."""

from __future__ import annotations

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger

log = get_logger(__name__)


def estimate_normals(pc: o3d.geometry.PointCloud, cfg: dict) -> None:
    """Estimate normals in place and orient them consistently where possible."""
    if len(pc.points) == 0:
        raise PipelineError(
            "Cannot estimate normals on an empty point cloud. "
            "Check the inputs in raw/ and the cleaning thresholds in the config."
        )
    knn = int(cfg_get(cfg, "mesh.normals.knn", 30))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=knn))
    try:
        # Slow on large clouds and can fail outright on noisy ones; unoriented
        # normals still give a usable (if rougher) Poisson result, so continue.
        pc.orient_normals_consistent_tangent_plane(30)
    except Exception as exc:
        log.warning("Consistent normal orientation failed (%s); continuing with unoriented normals", exc)


def _ball_pivot(pc: o3d.geometry.PointCloud) -> o3d.geometry.TriangleMesh:
    """Ball-pivoting fallback with radii derived from mean nearest-neighbour spacing."""
    distances = np.asarray(pc.compute_nearest_neighbor_distance())
    finite = distances[np.isfinite(distances)] if distances.size else distances
    if finite.size == 0 or float(np.mean(finite)) <= 0.0:
        raise PipelineError(
            "Point cloud is too sparse to estimate a ball-pivoting radius. "
            "Capture a denser scan or reduce clean.voxel_size."
        )
    mean_nn = float(np.mean(finite))
    radii = o3d.utility.DoubleVector([mean_nn * factor for factor in (1.5, 3.0, 6.0)])
    log.info("Ball pivoting with radii %.4f / %.4f / %.4f m", *[mean_nn * f for f in (1.5, 3.0, 6.0)])
    return o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pc, radii)


def pointcloud_to_mesh(pc: o3d.geometry.PointCloud, cfg: dict) -> o3d.geometry.TriangleMesh:
    """Reconstruct a surface: Poisson with density crop, ball pivoting as fallback."""
    if len(pc.points) == 0:
        raise PipelineError(
            "Cannot reconstruct a mesh from an empty point cloud. "
            "Check the inputs in raw/ and the cleaning thresholds in the config."
        )
    if not pc.has_normals():
        estimate_normals(pc, cfg)

    depth = int(cfg_get(cfg, "mesh.poisson.depth", 9))
    quantile = float(cfg_get(cfg, "mesh.poisson.density_quantile", 0.02))
    mesh: o3d.geometry.TriangleMesh | None = None
    try:
        log.info("Poisson reconstruction at depth %d", depth)
        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pc, depth=depth)
        densities = np.asarray(densities)
        if quantile > 0.0 and densities.size:
            threshold = float(np.quantile(densities, quantile))
            mesh.remove_vertices_by_mask(densities < threshold)
        # Poisson balloons past the data; crop to the cloud's bounds plus 2 percent,
        # with a millimetre of slack so near-flat clouds keep a non-zero crop box.
        aabb = pc.get_axis_aligned_bounding_box()
        centre = aabb.get_center()
        half = (aabb.get_max_bound() - aabb.get_min_bound()) * 0.5 * 1.02 + 1e-3
        mesh = mesh.crop(o3d.geometry.AxisAlignedBoundingBox(centre - half, centre + half))
    except Exception as exc:
        log.warning("Poisson reconstruction failed (%s); trying ball pivoting", exc)
        mesh = None

    if mesh is None or len(mesh.triangles) == 0:
        if mesh is not None:
            log.warning("Poisson produced no triangles after density crop; trying ball pivoting")
        mesh = _ball_pivot(pc)

    if len(mesh.triangles) == 0:
        raise PipelineError(
            "Surface reconstruction produced no triangles (Poisson and ball pivoting both failed). "
            "The cloud may be too sparse or too noisy: capture a denser scan, lower "
            "mesh.poisson.depth, or relax the cleaning thresholds."
        )
    mesh.compute_vertex_normals()
    log.info("Reconstructed mesh: %d vertices, %d triangles", len(mesh.vertices), len(mesh.triangles))
    return mesh


def repair_mesh(mesh: o3d.geometry.TriangleMesh, cfg: dict) -> tuple[o3d.geometry.TriangleMesh, dict]:
    """Basic Open3D cleanup always; pymeshlab hole closing and manifold repair when available."""
    if len(mesh.triangles) == 0:
        raise PipelineError(
            "Cannot repair an empty mesh. Reconstruction produced no triangles; "
            "check the earlier pipeline stages."
        )
    vertices_in = int(len(mesh.vertices))
    triangles_in = int(len(mesh.triangles))

    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_unreferenced_vertices()

    repair = "basic"
    try:
        import pymeshlab
    except ImportError:
        pymeshlab = None
        log.warning("pymeshlab not installed; skipping hole closing and non-manifold repair")

    if pymeshlab is not None:
        try:
            ms = pymeshlab.MeshSet()
            ms.add_mesh(pymeshlab.Mesh(
                vertex_matrix=np.asarray(mesh.vertices, dtype=np.float64),
                face_matrix=np.asarray(mesh.triangles, dtype=np.int32),
            ))
            ms.meshing_remove_duplicate_faces()
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_close_holes(maxholesize=30)
            fixed = ms.current_mesh()
            mesh = o3d.geometry.TriangleMesh(
                o3d.utility.Vector3dVector(np.asarray(fixed.vertex_matrix(), dtype=np.float64)),
                o3d.utility.Vector3iVector(np.asarray(fixed.face_matrix(), dtype=np.int32)),
            )
            mesh.remove_unreferenced_vertices()
            repair = "pymeshlab"
        except Exception as exc:
            log.warning("pymeshlab repair failed (%s); keeping the basic Open3D cleanup", exc)

    mesh.compute_vertex_normals()
    stats = {
        "repair": repair,
        "repair_vertices_in": vertices_in,
        "repair_triangles_in": triangles_in,
        "repair_vertices_out": int(len(mesh.vertices)),
        "repair_triangles_out": int(len(mesh.triangles)),
    }
    log.info("Repair (%s): %d -> %d triangles", repair, triangles_in, stats["repair_triangles_out"])
    return mesh, stats
