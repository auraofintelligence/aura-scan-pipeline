"""Asset analysis: oriented dimensions, plan footprint and key heights."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from shapely.geometry import MultiPoint, Polygon

from ..util import PipelineError, cfg_get, get_logger, o3d_mesh_to_trimesh

log = get_logger(__name__)


@dataclass
class AssetReport:
    dims_m: dict          # {"length": float, "width": float, "height": float}
    obb_centre: np.ndarray
    obb_axes: np.ndarray  # 3x3, rows are OBB axis directions in the world frame
    footprint_2d: np.ndarray
    key_heights_m: list[float]    # peaks of the vertex-height histogram (seat tops, table tops)
    stats: dict


def _footprint(verts: np.ndarray) -> np.ndarray:
    hull = MultiPoint(verts[:, :2]).convex_hull
    if not isinstance(hull, Polygon) or hull.area <= 0:
        raise PipelineError(
            "Mesh vertices are collinear in plan view; cannot outline a footprint. "
            "Check that the scan covers more than a single edge."
        )
    return np.asarray(hull.exterior.coords, dtype=np.float64)   # closed ring


def _key_heights(z_vals: np.ndarray, cfg: dict) -> list[float]:
    """Bin-centre heights of well-populated local maxima in the z histogram."""
    bins = int(cfg_get(cfg, "asset.key_height_bins", 60))
    min_fraction = float(cfg_get(cfg, "asset.key_height_min_fraction", 0.06))
    if bins < 1 or len(z_vals) == 0:
        return []
    counts, edges = np.histogram(z_vals, bins=bins)
    fractions = counts / float(len(z_vals))
    centres = (edges[:-1] + edges[1:]) / 2.0
    padded = np.concatenate(([-1], counts, [-1]))   # sentinel neighbours for the end bins
    return [
        float(centres[i])
        for i in range(len(counts))
        if fractions[i] >= min_fraction
        and counts[i] >= padded[i]
        and counts[i] >= padded[i + 2]
    ]   # centres ascend with the histogram, so the result is already sorted


def analyse_asset(mesh, cfg) -> AssetReport:
    """Oriented dimensions, footprint and key heights for the asset inventory."""
    tm = o3d_mesh_to_trimesh(mesh)
    verts = np.asarray(tm.vertices, dtype=np.float64)
    if len(verts) < 4:
        raise PipelineError(
            f"Asset analysis needs a real mesh (got {len(verts)} vertices). "
            "Check the mesh stage output."
        )

    to_origin, extents = trimesh.bounds.oriented_bounds(tm)
    extents = np.asarray(extents, dtype=np.float64)
    to_world = np.linalg.inv(to_origin)
    obb_centre = to_world[:3, 3].copy()
    # Rows are the OBB axis directions expressed in the world frame.
    obb_axes = to_world[:3, :3].T.copy()

    # Length and width are the two most horizontal OBB extents, length >= width;
    # height stays the world-Z extent so it reads as true standing height.
    vertical = int(np.argmax(np.abs(obb_axes[:, 2])))
    horizontal_extents = sorted(
        (float(extents[i]) for i in range(3) if i != vertical), reverse=True
    )
    z_vals = verts[:, 2]
    dims_m = {
        "length": horizontal_extents[0],
        "width": horizontal_extents[1],
        "height": float(z_vals.max() - z_vals.min()),
    }

    footprint_2d = _footprint(verts)
    key_heights = _key_heights(z_vals, cfg)

    stats = {
        "dims_m": dims_m,
        "key_heights_m": key_heights,
        "vertices": int(len(verts)),
    }
    log.info(
        "Asset dims %.3f x %.3f x %.3f m, %d key height(s)",
        dims_m["length"], dims_m["width"], dims_m["height"], len(key_heights),
    )
    return AssetReport(
        dims_m=dims_m,
        obb_centre=obb_centre,
        obb_axes=obb_axes,
        footprint_2d=footprint_2d,
        key_heights_m=key_heights,
        stats=stats,
    )
