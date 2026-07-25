"""Point cloud cleaning: outlier removal and JSON-safe stats."""

from __future__ import annotations

import json

import numpy as np
import open3d as o3d

from aura_scan.config import load_config
from aura_scan.ops.clean import clean_pointcloud

from synthetic import make_box_cloud

N_BOX = 20000
N_OUTLIERS = 60


def test_clean_pointcloud_removes_far_outliers() -> None:
    cfg = load_config("general")
    pc = make_box_cloud((1.0, 1.0, 1.0), n=N_BOX, noise=0.002, seed=7)

    # Scatter isolated outliers on a shell 5 to 9 metres out.
    rng = np.random.default_rng(13)
    directions = rng.normal(size=(N_OUTLIERS, 3))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    outliers = directions * rng.uniform(5.0, 9.0, (N_OUTLIERS, 1))
    combined = np.vstack([np.asarray(pc.points), outliers])
    noisy = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(combined))

    cleaned, stats = clean_pointcloud(noisy, cfg)

    kept = np.asarray(cleaned.points)
    assert len(kept) > 0
    # Every far outlier must be gone; box points sit within about 0.87 m.
    assert float(np.max(np.linalg.norm(kept, axis=1))) < 2.0

    assert stats["points_in"] == N_BOX + N_OUTLIERS
    assert stats["points_out"] == len(kept)
    assert stats["points_out"] < stats["points_in"]
    assert stats["removed_outliers"] >= N_OUTLIERS
    json.dumps(stats)  # stats must be JSON-serialisable, no NumPy scalars
