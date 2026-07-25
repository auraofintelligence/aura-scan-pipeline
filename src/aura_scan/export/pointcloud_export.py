"""Point cloud exports: PLY always, LAS when laspy can write it."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, ensure_dir, get_logger

log = get_logger(__name__)


def export_pointcloud(pc: o3d.geometry.PointCloud, out_dir: Path,
                      basename: str, cfg: dict) -> dict[str, str]:
    """Write the merged cloud; return only the files actually written."""
    out_dir = ensure_dir(Path(out_dir))
    if len(pc.points) == 0:
        raise PipelineError(
            "The merged point cloud is empty, nothing to export. "
            "Check the inputs in raw/ and loosen the clean.* thresholds."
        )
    formats = [
        str(f).lower()
        for f in (cfg_get(cfg, "export.pointcloud_formats", ["ply", "las"]) or [])
    ]
    written: dict[str, str] = {}

    # PLY is the archival format and is always written.
    ply_name = f"{basename}_points.ply"
    if "ply" not in formats:
        log.info("PLY missing from export.pointcloud_formats; writing it anyway")
    if not o3d.io.write_point_cloud(str(out_dir / ply_name), pc):
        raise PipelineError(
            f"Open3D could not write {out_dir / ply_name}. Is the folder writable?"
        )
    written["pointcloud_ply"] = ply_name
    log.info("Wrote %s (%d points)", ply_name, len(pc.points))

    if "las" in formats:
        las_name = f"{basename}_points.las"
        if _write_las(pc, out_dir / las_name):
            written["pointcloud_las"] = las_name
    return written


def _write_las(pc: o3d.geometry.PointCloud, path: Path) -> bool:
    try:
        import laspy
    except ImportError:
        log.warning(
            "laspy is not installed; skipping the LAS export. "
            "Install it with: uv pip install laspy"
        )
        return False

    points = np.asarray(pc.points, dtype=np.float64)
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.offsets = points.min(axis=0)
    header.scales = np.array([0.001, 0.001, 0.001])  # millimetre resolution
    las = laspy.LasData(header)
    las.x = points[:, 0]
    las.y = points[:, 1]
    las.z = points[:, 2]
    if pc.has_colors():
        # Open3D colours are floats in 0..1; LAS point format 3 stores uint16.
        colours = np.clip(np.asarray(pc.colors, dtype=np.float64), 0.0, 1.0)
        rgb = np.round(colours * 65535.0).astype(np.uint16)
        las.red = rgb[:, 0]
        las.green = rgb[:, 1]
        las.blue = rgb[:, 2]
    try:
        las.write(str(path))
    except Exception as exc:
        log.warning("LAS export failed (%s); the PLY export still stands", exc)
        return False
    log.info("Wrote %s (%d points)", path.name, len(points))
    return True
