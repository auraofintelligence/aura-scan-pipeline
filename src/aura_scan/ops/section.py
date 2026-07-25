"""Section cutting: plan cut plus long and cross sections through a mesh."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from ..util import PipelineError, cfg_get, get_logger, o3d_mesh_to_trimesh

log = get_logger(__name__)


@dataclass
class SectionSpec:
    name: str
    origin: np.ndarray   # (3,)
    normal: np.ndarray   # (3,) unit


@dataclass
class SectionResult:
    spec: SectionSpec
    polylines: list[np.ndarray]   # each (N,2), metres, in the section plane's 2D frame
    extent_2d: tuple[float, float]


def _dominant_horizontal_axes(tm: trimesh.Trimesh) -> list[tuple[np.ndarray, float]]:
    """The two most horizontal oriented-bounds axes, paired with their extents.

    Each axis is projected onto the XY plane and normalised. Returns an empty
    list when the projection is degenerate (axes near-vertical or the two
    projections near-parallel), so the caller can fall back to world X and Y.
    """
    to_origin, extents = trimesh.bounds.oriented_bounds(tm)
    axes = to_origin[:3, :3]   # rows are OBB axis directions in the world frame
    order = np.argsort(-np.linalg.norm(axes[:, :2], axis=1))
    picked: list[tuple[np.ndarray, float]] = []
    for i in order[:2]:
        xy = np.array([axes[i, 0], axes[i, 1], 0.0])
        norm = float(np.linalg.norm(xy))
        if norm < 1e-6:
            continue
        picked.append((xy / norm, float(extents[i])))
    if len(picked) == 2 and abs(float(np.dot(picked[0][0], picked[1][0]))) < 0.99:
        return picked
    return []


def auto_section_specs(mesh, cfg) -> list[SectionSpec]:
    """Plan, long-section and cross-section specs derived from the mesh bounds."""
    tm = o3d_mesh_to_trimesh(mesh)
    if len(tm.vertices) == 0 or len(tm.faces) == 0:
        raise PipelineError(
            "Cannot plan sections: the mesh is empty. Check the mesh stage output."
        )

    bounds = np.asarray(tm.bounds, dtype=np.float64)   # (2, 3) min/max
    min_z, max_z = float(bounds[0, 2]), float(bounds[1, 2])
    height = max_z - min_z
    centre = bounds.mean(axis=0)

    specs: list[SectionSpec] = []

    plan_height = float(cfg_get(cfg, "sections.plan_height", 0.5))
    if height <= 0.0:
        log.warning("Mesh has no vertical extent, skipping the plan section")
    else:
        plan_z = min_z + plan_height
        if plan_z >= max_z:
            plan_z = min_z + 0.5 * height
            log.info(
                "Plan height %.2f m exceeds the mesh height %.2f m, cutting at mid-height",
                plan_height, height,
            )
        specs.append(SectionSpec(
            name="plan",
            origin=np.array([centre[0], centre[1], plan_z]),
            normal=np.array([0.0, 0.0, 1.0]),
        ))

    pairs = _dominant_horizontal_axes(tm)
    if len(pairs) < 2:
        log.info("Oriented bounds degenerate in plan view, using world X and Y for sections")
        extent = bounds[1] - bounds[0]
        pairs = [
            (np.array([1.0, 0.0, 0.0]), float(extent[0])),
            (np.array([0.0, 1.0, 0.0]), float(extent[1])),
        ]
    pairs.sort(key=lambda p: -p[1])
    long_dir, short_dir = pairs[0][0], pairs[1][0]
    # The long section runs along the long axis, so its plane normal is the
    # short horizontal direction; the cross section cuts across the long axis.
    specs.append(SectionSpec(name="long-section", origin=centre.copy(), normal=short_dir))
    specs.append(SectionSpec(name="cross-section", origin=centre.copy(), normal=long_dir))
    return specs


def cut_sections(mesh, specs: list[SectionSpec], cfg) -> list[SectionResult]:
    """Cut each spec's plane through the mesh; specs with no intersection are skipped."""
    tm = o3d_mesh_to_trimesh(mesh)
    if len(tm.faces) == 0:
        raise PipelineError(
            "Cannot cut sections: the mesh has no faces. Check the mesh stage output."
        )

    results: list[SectionResult] = []
    for spec in specs:
        path3 = tm.section(
            plane_origin=np.asarray(spec.origin, dtype=np.float64),
            plane_normal=np.asarray(spec.normal, dtype=np.float64),
        )
        if path3 is None or len(path3.entities) == 0:
            log.info("Section '%s' does not intersect the mesh, skipped", spec.name)
            continue
        try:
            path2, _to_3d = path3.to_2D()
        except AttributeError:   # older trimesh
            path2, _to_3d = path3.to_planar()
        polylines = [np.asarray(d, dtype=np.float64) for d in path2.discrete if len(d) >= 2]
        if not polylines:
            log.info("Section '%s' produced no polylines, skipped", spec.name)
            continue
        stacked = np.vstack(polylines)
        mins = stacked.min(axis=0)
        maxs = stacked.max(axis=0)
        results.append(SectionResult(
            spec=spec,
            polylines=polylines,
            extent_2d=(float(maxs[0] - mins[0]), float(maxs[1] - mins[1])),
        ))
        log.info(
            "Section '%s': %d polyline(s), extent %.2f x %.2f m",
            spec.name, len(polylines), results[-1].extent_2d[0], results[-1].extent_2d[1],
        )
    return results
