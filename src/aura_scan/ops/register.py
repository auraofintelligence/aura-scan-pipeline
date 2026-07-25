"""Registration: point-to-plane ICP, sequential cloud merging, Umeyama alignment.

Multiple exports of the same subject are aligned to the first cloud and
concatenated. Umeyama gives the closed-form rigid (or similarity) transform
between matched point sets, used by control-point workflows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger

log = get_logger(__name__)


@dataclass
class RegResult:
    transform: np.ndarray  # 4x4
    fitness: float
    inlier_rmse: float


def _ensure_normals(pc: o3d.geometry.PointCloud, cfg: dict) -> None:
    """Estimate normals in place when absent; point-to-plane ICP needs them."""
    if pc.has_normals():
        return
    knn = int(cfg_get(cfg, "mesh.normals.knn", 30))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=knn))


def register_icp(
    source_pc: o3d.geometry.PointCloud,
    target_pc: o3d.geometry.PointCloud,
    cfg: dict,
    init: np.ndarray | None = None,
) -> RegResult:
    """Point-to-plane ICP aligning source onto target.

    Normals are estimated on either cloud when absent. The initial transform
    defaults to identity; ARKit exports of the same subject start near-aligned.
    """
    if len(source_pc.points) == 0 or len(target_pc.points) == 0:
        raise PipelineError(
            "Cannot register an empty point cloud; check the exports in raw/."
        )
    _ensure_normals(source_pc, cfg)
    _ensure_normals(target_pc, cfg)

    max_correspondence = float(cfg_get(cfg, "register.icp.max_correspondence", 0.05))
    max_iterations = int(cfg_get(cfg, "register.icp.max_iterations", 60))
    init_t = np.eye(4) if init is None else np.asarray(init, dtype=float)

    result = o3d.pipelines.registration.registration_icp(
        source_pc,
        target_pc,
        max_correspondence,
        init_t,
        o3d.pipelines.registration.TransformationEstimationPointToPlane(),
        o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=max_iterations),
    )
    return RegResult(
        transform=np.asarray(result.transformation, dtype=float).copy(),
        fitness=float(result.fitness),
        inlier_rmse=float(result.inlier_rmse),
    )


def merge_pointclouds(
    pcs: list, cfg: dict
) -> tuple[o3d.geometry.PointCloud, list[RegResult]]:
    """Register clouds[1:] onto clouds[0], concatenate, then voxel dedupe.

    Each ICP starts from identity. Colours and normals are carried through only
    when every cloud has them; a partial set cannot be concatenated coherently.
    """
    if not pcs:
        raise PipelineError("No point clouds to merge; ingest produced nothing.")
    if len(pcs) == 1:
        return pcs[0], []

    target = pcs[0]
    aligned: list[o3d.geometry.PointCloud] = [target]
    results: list[RegResult] = []
    for i, pc in enumerate(pcs[1:], start=2):
        reg = register_icp(pc, target, cfg, init=np.eye(4))
        log.info(
            "Registered cloud %d/%d: fitness %.3f, inlier RMSE %.4f m",
            i, len(pcs), reg.fitness, reg.inlier_rmse,
        )
        moved = o3d.geometry.PointCloud(pc)
        moved.transform(reg.transform)
        aligned.append(moved)
        results.append(reg)

    merged = o3d.geometry.PointCloud()
    merged.points = o3d.utility.Vector3dVector(
        np.vstack([np.asarray(p.points) for p in aligned])
    )
    if all(p.has_colors() for p in aligned):
        merged.colors = o3d.utility.Vector3dVector(
            np.vstack([np.asarray(p.colors) for p in aligned])
        )
    if all(p.has_normals() for p in aligned):
        merged.normals = o3d.utility.Vector3dVector(
            np.vstack([np.asarray(p.normals) for p in aligned])
        )

    voxel_size = float(cfg_get(cfg, "clean.voxel_size", 0.005) or 0.0)
    if voxel_size > 0:
        before = len(merged.points)
        merged = merged.voxel_down_sample(voxel_size)
        log.info(
            "Voxel dedupe at %.3f m: %d -> %d points",
            voxel_size, before, len(merged.points),
        )
    return merged, results


def umeyama(
    src: np.ndarray, dst: np.ndarray, with_scale: bool = False
) -> tuple[np.ndarray, float, np.ndarray]:
    """Closed-form Umeyama alignment of matched (N, 3) point sets.

    Returns (T, scale, residuals): the 4x4 transform mapping src onto dst, the
    uniform scale (1.0 when with_scale is False), and per-point residual
    distances after applying T to src.
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.ndim != 2 or src.shape[1] != 3 or src.shape != dst.shape:
        raise PipelineError(
            f"Umeyama needs two matching (N, 3) arrays, got {src.shape} and {dst.shape}"
        )
    n = src.shape[0]
    if n < 3:
        raise PipelineError(
            f"Umeyama alignment needs at least 3 point pairs, got {n}. "
            "Pick more control points."
        )

    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst
    var_src = float((src_c ** 2).sum() / n)
    if var_src < 1e-18:
        raise PipelineError(
            "Umeyama alignment failed: the source points are coincident. "
            "Pick control points that are spread apart."
        )

    cov = dst_c.T @ src_c / n
    u, d, vt = np.linalg.svd(cov)
    # Reflection guard: force a proper rotation (det +1).
    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[2, 2] = -1.0
    rotation = u @ s @ vt
    scale = float((d * np.diag(s)).sum() / var_src) if with_scale else 1.0
    translation = mu_dst - scale * rotation @ mu_src

    transform = np.eye(4)
    transform[:3, :3] = scale * rotation
    transform[:3, 3] = translation
    moved = src @ (scale * rotation).T + translation
    residuals = np.linalg.norm(moved - dst, axis=1)
    return transform, scale, residuals
