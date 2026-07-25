"""Pipeline orchestration: ingest to exports, per DESIGN.md stage order."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import __version__
from .config import load_config
from .project import ScanProject
from .util import PipelineError, cfg_get, get_logger, np_to_list

log = get_logger(__name__)


def _build_constraints(project: ScanProject):
    from .ops.scale import DistanceConstraint

    points = [np.asarray(p, dtype=float) for p in project.picked_points()]
    constraints = []
    for entry in project.distance_constraint_dicts():
        idx = entry.get("points")
        if not idx or len(idx) != 2:
            raise PipelineError(f"distance_constraints entry needs points: [i, j]: {entry}")
        i, j = int(idx[0]), int(idx[1])
        if not (0 <= i < len(points) and 0 <= j < len(points)):
            raise PipelineError(
                f"distance_constraints entry {entry} references picked point indices "
                f"outside 0..{len(points) - 1}"
            )
        constraints.append(DistanceConstraint(
            a=points[i], b=points[j],
            distance_m=float(entry["distance_m"]),
            note=str(entry.get("note", "")),
        ))
    return constraints


def _extra_section_specs(project: ScanProject):
    from .ops.section import SectionSpec

    specs = []
    for entry in project.extra_section_dicts():
        normal = np.asarray(entry["normal"], dtype=float)
        norm = np.linalg.norm(normal)
        if norm == 0:
            raise PipelineError(f"Section {entry} has a zero normal")
        specs.append(SectionSpec(
            name=str(entry.get("name", f"section-{len(specs) + 1}")),
            origin=np.asarray(entry["origin"], dtype=float),
            normal=normal / norm,
        ))
    return specs


def run_scan(project: ScanProject, cfg: dict | None = None) -> dict:
    from .io.ingest import discover_inputs, load_input, to_pointcloud
    from .ops.asset import analyse_asset
    from .ops.clean import clean_mesh, clean_pointcloud
    from .ops.meshing import pointcloud_to_mesh, repair_mesh
    from .ops.plane import analyse_projection_surface
    from .ops.register import merge_pointclouds
    from .ops.scale import apply_scale, solve_scale, verification_block
    from .ops.section import auto_section_specs, cut_sections
    from .ops.simplify import make_lods
    from .export.dxf import export_dxf
    from .export.mesh_export import export_lods
    from .export.metadata import build_metadata, write_metadata
    from .export.pointcloud_export import export_pointcloud
    from .export.web import export_web

    mode = project.mode
    if cfg is None:
        cfg = load_config(mode, project.manifest.get("config") or {})
    stage_stats: dict = {}

    # 1. Ingest
    paths = discover_inputs(project.raw_dir)
    if not paths:
        raise PipelineError(
            f"No inputs found in {project.raw_dir}. Drop a phone export "
            "(OBJ, PLY, GLB, LAS or a raw depth package) into raw/ first."
        )
    log.info("Ingesting %d input(s) from %s", len(paths), project.raw_dir)
    items = [load_input(p, cfg) for p in paths]
    stage_stats["ingest"] = {"inputs": [str(p.name) for p in paths],
                             "kinds": [it.kind for it in items]}

    # 2. Clean point clouds
    clouds = []
    for item in items:
        pc = to_pointcloud(item, cfg)
        pc, stats = clean_pointcloud(pc, cfg)
        clouds.append(pc)
        stage_stats.setdefault("clean", []).append(stats)

    # 3. Register and merge
    if len(clouds) > 1:
        log.info("Registering %d clouds", len(clouds))
        merged, reg_results = merge_pointclouds(clouds, cfg)
        stage_stats["register"] = [
            {"fitness": r.fitness, "inlier_rmse": r.inlier_rmse} for r in reg_results
        ]
    else:
        merged = clouds[0]

    # 4. Scale verification
    constraints = _build_constraints(project)
    scale_result, scale_applied = None, False
    if constraints:
        scale_result = solve_scale(constraints)
        tolerance = cfg_get(cfg, "scale.apply_tolerance", 0.002)
        if abs(scale_result.factor - 1.0) > tolerance:
            log.info("Applying scale factor %.5f", scale_result.factor)
            apply_scale(merged, scale_result.factor)
            scale_applied = True
        else:
            log.info("Scale verified within tolerance (factor %.5f), not rescaling",
                     scale_result.factor)
    scale_block = verification_block(scale_result, scale_applied)
    stage_stats["scale"] = scale_block

    # 5. Mesh
    source_meshes = [it.mesh for it in items if it.mesh is not None]
    if source_meshes and cfg_get(cfg, "mesh.prefer_source_mesh", True) and len(items) == 1:
        log.info("Using the capture app's mesh")
        mesh, mesh_stats = clean_mesh(source_meshes[0], cfg)
        if scale_applied:
            apply_scale(mesh, scale_result.factor)
    else:
        log.info("Reconstructing mesh from the merged cloud")
        mesh = pointcloud_to_mesh(merged, cfg)
        mesh_stats = {"source": "reconstructed"}
    mesh, repair_stats = repair_mesh(mesh, cfg)
    stage_stats["mesh"] = {**mesh_stats, **repair_stats}

    # 6. LOD ladder
    lods = make_lods(mesh, cfg)
    stage_stats["lods"] = lods.stats

    # 7. Mode-specific analysis (dimensions are useful in every mode)
    asset_report = analyse_asset(mesh, cfg)
    stage_stats["asset"] = asset_report.stats
    plane_report = None
    if mode == "projection_surface":
        plane_report = analyse_projection_surface(merged, cfg)
        stage_stats["projection"] = {
            "rmse_m": plane_report.rmse_m,
            "inlier_ratio": plane_report.inlier_ratio,
            "usable_area_m2": plane_report.usable_area_m2,
            "suitable": plane_report.suitable,
        }

    # 8. Sections
    specs = []
    if cfg_get(cfg, "sections.auto", True):
        specs.extend(auto_section_specs(mesh, cfg))
    specs.extend(_extra_section_specs(project))
    sections = cut_sections(mesh, specs, cfg)
    stage_stats["sections"] = [s.spec.name for s in sections]

    # 9. Exports
    basename = project.name.replace(" ", "-").lower()
    geometry_files = {}
    geometry_files.update(export_lods(lods, project.exports_dir, basename))
    geometry_files.update(export_pointcloud(merged, project.exports_dir, basename, cfg))

    meta_lines = [
        f"Aura Scan Pipeline v{__version__}  scan: {project.name}  mode: {mode}",
        f"Local frame: {cfg_get(project.manifest, 'location.local_frame', '') or 'unnamed'}"
        "  units noted per header  Z up",
        f"Scale: {scale_block.get('method')}  factor {scale_block.get('factor_applied')}"
        f"  verified: {scale_block.get('verified')}",
    ]
    dxf_path = export_dxf(
        project.exports_dir / f"{basename}.dxf",
        footprint_2d=lods.footprint_2d,
        sections=sections,
        asset_report=asset_report,
        plane_report=plane_report,
        meta_lines=meta_lines,
        cfg=cfg,
    )
    geometry_files["dxf"] = dxf_path.name

    meta = build_metadata(
        project, cfg,
        scale_block=scale_block,
        asset_report=asset_report,
        plane_report=plane_report,
        geometry_files=geometry_files,
        stage_stats=stage_stats,
    )
    meta_path = write_metadata(meta, project.exports_dir)
    web_files = export_web(project, meta, lods, project.exports_dir, cfg)

    summary = np_to_list({
        "scan": project.name,
        "mode": mode,
        "dimensions_m": asset_report.dims_m,
        "scale": scale_block,
        "exports": {**geometry_files, "metadata": meta_path.name, **web_files},
        "stages": stage_stats,
    })
    with open(project.exports_dir / "run-summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    log.info("Done. Exports in %s", project.exports_dir)
    return summary
