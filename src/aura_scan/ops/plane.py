"""Projection-surface analysis: RANSAC plane fit, planarity and usable area."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import open3d as o3d
import shapely
import trimesh
from shapely.geometry import MultiPoint, Polygon

from ..util import PipelineError, cfg_get, get_logger, trimesh_to_o3d

log = get_logger(__name__)

# Cap the hull input so GEOS stays responsive on dense clouds; the RMSE and
# inlier ratio are still computed on every inlier.
_HULL_MAX_POINTS = 60_000


@dataclass
class PlaneReport:
    origin: np.ndarray
    normal: np.ndarray
    rmse_m: float
    inlier_ratio: float
    boundary_2d: np.ndarray        # (N,2) usable-area outline in the plane frame
    usable_area_m2: float
    suitable: bool
    plane_mesh: o3d.geometry.TriangleMesh


def _plane_frame(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two orthonormal in-plane axes completing a right-handed frame with the normal."""
    helper = np.array([0.0, 0.0, 1.0]) if abs(float(normal[2])) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(helper, normal)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return u, v


def _boundary_polygon(coords_2d: np.ndarray) -> Polygon:
    """Usable-area outline: concave hull, falling back to the convex hull."""
    pts = coords_2d
    if len(pts) > _HULL_MAX_POINTS:
        rng = np.random.default_rng(0)
        pts = pts[rng.choice(len(pts), _HULL_MAX_POINTS, replace=False)]
    multipoint = MultiPoint(pts)
    polygon: Polygon | None = None
    try:
        hull = shapely.concave_hull(multipoint, ratio=0.3)
        if isinstance(hull, Polygon) and hull.area > 0:
            polygon = hull
    except Exception as exc:
        log.warning("concave_hull failed (%s), falling back to the convex hull", exc)
    if polygon is None:
        hull = multipoint.convex_hull
        if not isinstance(hull, Polygon) or hull.area <= 0:
            raise PipelineError(
                "Plane inliers are collinear; cannot outline a usable area. "
                "The scan may cover only a sliver of the surface, capture a wider sweep."
            )
        polygon = hull
    simplified = polygon.simplify(0.02)
    if isinstance(simplified, Polygon) and simplified.area > 0:
        polygon = simplified
    return polygon


def _delaunay_triangulate(polygon: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """Triangulate the outline with SciPy, keeping triangles inside the polygon."""
    from scipy.spatial import Delaunay

    ring = np.asarray(polygon.exterior.coords, dtype=np.float64)[:-1]
    if len(ring) < 3:
        raise PipelineError(
            "Usable-area outline has fewer than three corners; cannot mesh the plane."
        )
    tri = Delaunay(ring)
    centroids = ring[tri.simplices].mean(axis=1)
    shapely.prepare(polygon)
    keep = shapely.contains_xy(polygon, centroids[:, 0], centroids[:, 1])
    faces = tri.simplices[keep]
    if len(faces) == 0:
        faces = tri.simplices   # sliver polygon: better a convex fill than nothing
    return ring, faces


def _polygon_to_mesh(polygon: Polygon, origin: np.ndarray,
                     u: np.ndarray, v: np.ndarray) -> o3d.geometry.TriangleMesh:
    try:
        verts_2d, faces = trimesh.creation.triangulate_polygon(polygon)
    except Exception as exc:
        # No earcut/triangle engine available on this box.
        log.warning("trimesh triangulation unavailable (%s), using Delaunay fallback", exc)
        verts_2d, faces = _delaunay_triangulate(polygon)
    verts_2d = np.asarray(verts_2d, dtype=np.float64)
    verts_3d = origin[None, :] + np.outer(verts_2d[:, 0], u) + np.outer(verts_2d[:, 1], v)
    flat = trimesh.Trimesh(vertices=verts_3d, faces=np.asarray(faces), process=False)
    return trimesh_to_o3d(flat)


def analyse_projection_surface(pc, cfg) -> PlaneReport:
    """Fit the dominant plane and judge its suitability for projection art."""
    points = np.asarray(pc.points, dtype=np.float64)
    if len(points) < 100:
        raise PipelineError(
            f"Projection-surface analysis needs a denser cloud (got {len(points)} points). "
            "Capture the surface again with slower, closer passes."
        )

    distance_threshold = float(cfg_get(cfg, "projection.ransac.distance_threshold", 0.01))
    num_iterations = int(cfg_get(cfg, "projection.ransac.num_iterations", 2000))
    model, inlier_idx = pc.segment_plane(
        distance_threshold=distance_threshold,
        ransac_n=3,
        num_iterations=num_iterations,
    )
    inlier_idx = np.asarray(inlier_idx, dtype=np.int64)
    if inlier_idx.size < 3:
        raise PipelineError(
            "RANSAC found no dominant plane in the cloud. The surface may be too curved "
            "or too sparse; try raising projection.ransac.distance_threshold."
        )

    normal = np.asarray(model[:3], dtype=np.float64)
    norm = float(np.linalg.norm(normal))
    if norm == 0.0:
        raise PipelineError("RANSAC returned a degenerate plane normal; the cloud may be corrupt.")
    normal = normal / norm
    d = float(model[3]) / norm

    inliers = points[inlier_idx]
    signed = inliers @ normal + d
    rmse = float(np.sqrt(np.mean(signed ** 2)))
    inlier_ratio = float(inlier_idx.size) / float(len(points))

    centroid_in = inliers.mean(axis=0)
    origin = centroid_in - (float(centroid_in @ normal) + d) * normal

    # Orient the normal towards the open side: away from the bulk of the cloud.
    # An ambiguous (near-zero) offset keeps the normal as computed.
    side = float((points.mean(axis=0) - origin) @ normal)
    if side > 1e-6:
        normal = -normal

    u, v = _plane_frame(normal)
    rel = inliers - origin
    coords_2d = np.column_stack([rel @ u, rel @ v])

    polygon = _boundary_polygon(coords_2d)
    boundary_2d = np.asarray(polygon.exterior.coords, dtype=np.float64)
    usable_area = float(polygon.area)
    plane_mesh = _polygon_to_mesh(polygon, origin, u, v)

    max_rmse = float(cfg_get(cfg, "projection.max_rmse", 0.015))
    min_inlier_ratio = float(cfg_get(cfg, "projection.min_inlier_ratio", 0.6))
    suitable = bool(rmse <= max_rmse and inlier_ratio >= min_inlier_ratio)

    log.info(
        "Plane fit: RMSE %.4f m, inliers %.1f%%, usable area %.2f m2, suitable: %s",
        rmse, inlier_ratio * 100.0, usable_area, suitable,
    )
    return PlaneReport(
        origin=origin,
        normal=normal,
        rmse_m=rmse,
        inlier_ratio=inlier_ratio,
        boundary_2d=boundary_2d,
        usable_area_m2=usable_area,
        suitable=suitable,
        plane_mesh=plane_mesh,
    )
