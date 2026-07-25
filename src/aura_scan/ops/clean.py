"""Point cloud and mesh cleaning: downsampling, outlier removal, largest cluster.

Every operation reads its thresholds from the merged config and degrades
gracefully: a missing key falls back to the defaults in configs/default.yaml,
and clouds too small for an outlier filter skip it with a logged warning.
"""

from __future__ import annotations

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger

log = get_logger(__name__)

# DBSCAN clusters smaller than this are treated as noise when keeping the
# largest cluster; fixed by design, only the radius (clean.cluster_eps) tunes.
_DBSCAN_MIN_POINTS = 10


def _keep_largest_cluster_pc(
    pc: o3d.geometry.PointCloud, eps: float
) -> tuple[o3d.geometry.PointCloud, int, int]:
    """Return (cloud, points_removed, clusters_found) after a DBSCAN largest-cluster keep."""
    labels = np.asarray(pc.cluster_dbscan(eps=eps, min_points=_DBSCAN_MIN_POINTS))
    if labels.size == 0 or labels.max() < 0:
        log.warning(
            "DBSCAN found no cluster at eps=%.3f m (all %d points are noise); "
            "keeping the cloud unchanged. Raise clean.cluster_eps if this is wrong.",
            eps, labels.size,
        )
        return pc, 0, 0
    counts = np.bincount(labels[labels >= 0])
    clusters_found = int(counts.size)
    best = int(np.argmax(counts))
    keep = np.flatnonzero(labels == best)
    removed = int(len(pc.points) - keep.size)
    if removed:
        pc = pc.select_by_index(keep.tolist())
        log.info(
            "Kept largest of %d cluster(s): %d points kept, %d removed",
            clusters_found, keep.size, removed,
        )
    return pc, removed, clusters_found


def clean_pointcloud(
    pc: o3d.geometry.PointCloud, cfg: dict
) -> tuple[o3d.geometry.PointCloud, dict]:
    """Voxel downsample, remove outliers, optionally keep the largest cluster.

    Returns the cleaned cloud and a JSON-serialisable stats dict.
    """
    points_in = int(len(pc.points))
    if points_in == 0:
        raise PipelineError(
            "Point cloud is empty. Check the export in raw/: the capture may have "
            "failed, or the file may be corrupt or in an unexpected unit."
        )
    stats: dict = {"points_in": points_in}

    voxel_size = float(cfg_get(cfg, "clean.voxel_size", 0.005) or 0.0)
    if voxel_size > 0:
        pc = pc.voxel_down_sample(voxel_size)
        stats["voxel_size_m"] = voxel_size
        stats["points_after_voxel"] = int(len(pc.points))

    removed_statistical = 0
    if cfg_get(cfg, "clean.statistical_outlier.enabled", True):
        nb_neighbors = int(cfg_get(cfg, "clean.statistical_outlier.nb_neighbors", 20))
        std_ratio = float(cfg_get(cfg, "clean.statistical_outlier.std_ratio", 2.0))
        if len(pc.points) < nb_neighbors:
            log.warning(
                "Cloud has only %d points, fewer than nb_neighbors=%d; "
                "skipping statistical outlier removal",
                len(pc.points), nb_neighbors,
            )
        else:
            before = len(pc.points)
            pc, _ = pc.remove_statistical_outlier(
                nb_neighbors=nb_neighbors, std_ratio=std_ratio
            )
            removed_statistical = int(before - len(pc.points))

    removed_radius = 0
    if cfg_get(cfg, "clean.radius_outlier.enabled", False):
        nb_points = int(cfg_get(cfg, "clean.radius_outlier.nb_points", 16))
        radius = float(cfg_get(cfg, "clean.radius_outlier.radius", 0.05))
        if len(pc.points) < nb_points:
            log.warning(
                "Cloud has only %d points, fewer than nb_points=%d; "
                "skipping radius outlier removal",
                len(pc.points), nb_points,
            )
        else:
            before = len(pc.points)
            pc, _ = pc.remove_radius_outlier(nb_points=nb_points, radius=radius)
            removed_radius = int(before - len(pc.points))

    removed_cluster = 0
    clusters_found = 0
    if cfg_get(cfg, "clean.keep_largest_cluster", False) and len(pc.points) > 0:
        eps = float(cfg_get(cfg, "clean.cluster_eps", 0.05))
        pc, removed_cluster, clusters_found = _keep_largest_cluster_pc(pc, eps)

    points_out = int(len(pc.points))
    if points_out == 0:
        raise PipelineError(
            "Cleaning removed every point. Relax the clean.* thresholds in the "
            "config (larger std_ratio, smaller voxel_size, or disable filters)."
        )

    stats.update({
        "points_out": points_out,
        "removed_statistical": removed_statistical,
        "removed_radius": removed_radius,
        "removed_outliers": removed_statistical + removed_radius,
        "removed_cluster": removed_cluster,
        "clusters_found": clusters_found,
    })
    log.info("Cleaned cloud: %d -> %d points", points_in, points_out)
    return pc, stats


def clean_mesh(
    mesh: o3d.geometry.TriangleMesh, cfg: dict
) -> tuple[o3d.geometry.TriangleMesh, dict]:
    """Remove duplicated, degenerate and unreferenced elements; optionally keep
    the largest connected component (by surface area).

    Works on a copy so the caller's mesh is untouched. Returns the cleaned mesh
    and a JSON-serialisable stats dict.
    """
    triangles_in = int(len(mesh.triangles))
    vertices_in = int(len(mesh.vertices))
    if triangles_in == 0 or vertices_in == 0:
        raise PipelineError(
            "Mesh has no geometry. Check the export in raw/, or set "
            "mesh.prefer_source_mesh: false to reconstruct from the point cloud."
        )

    mesh = o3d.geometry.TriangleMesh(mesh)
    mesh.remove_duplicated_vertices()
    mesh.remove_duplicated_triangles()
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()

    components_found = 0
    removed_components = 0
    if cfg_get(cfg, "clean.keep_largest_cluster", False) and len(mesh.triangles) > 0:
        cluster_ids, _cluster_sizes, areas = mesh.cluster_connected_triangles()
        cluster_ids = np.asarray(cluster_ids)
        areas = np.asarray(areas, dtype=float)
        components_found = int(areas.size)
        if components_found > 1:
            best = int(np.argmax(areas))
            mesh.remove_triangles_by_mask((cluster_ids != best).tolist())
            mesh.remove_unreferenced_vertices()
            removed_components = components_found - 1
            log.info(
                "Kept largest of %d component(s) by area (%.4f m^2)",
                components_found, float(areas[best]),
            )

    triangles_out = int(len(mesh.triangles))
    vertices_out = int(len(mesh.vertices))
    if triangles_out == 0:
        raise PipelineError(
            "Mesh cleaning removed every triangle; the source mesh is degenerate. "
            "Set mesh.prefer_source_mesh: false to reconstruct from the point cloud."
        )
    mesh.compute_vertex_normals()

    stats = {
        "triangles_in": triangles_in,
        "triangles_out": triangles_out,
        "vertices_in": vertices_in,
        "vertices_out": vertices_out,
        "components_found": components_found,
        "removed_components": removed_components,
    }
    log.info("Cleaned mesh: %d -> %d triangles", triangles_in, triangles_out)
    return mesh, stats
