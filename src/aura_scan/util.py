"""Shared helpers: errors, logging, config access, geometry conversion."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


class PipelineError(Exception):
    """A user-fixable failure with an actionable message."""


_LOG_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        _LOG_CONFIGURED = True
    return logging.getLogger(name)


def cfg_get(cfg: dict, dotted: str, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def np_to_list(value):
    """Recursively cast NumPy values so a structure is JSON-serialisable."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, dict):
        return {k: np_to_list(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [np_to_list(v) for v in value]
    return value


def o3d_mesh_to_trimesh(mesh):
    import trimesh

    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices),
        faces=np.asarray(mesh.triangles),
        process=False,
    )


def trimesh_to_o3d(tm):
    import open3d as o3d

    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(np.asarray(tm.vertices, dtype=np.float64)),
        o3d.utility.Vector3iVector(np.asarray(tm.faces, dtype=np.int32)),
    )
    mesh.compute_vertex_normals()
    return mesh
