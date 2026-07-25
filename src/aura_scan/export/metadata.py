"""Metadata sidecar: the schema 2.0 JSON record every export carries."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .. import __version__
from ..util import PipelineError, cfg_get, ensure_dir, get_logger, now_iso, np_to_list

if TYPE_CHECKING:
    from ..ops.asset import AssetReport
    from ..ops.plane import PlaneReport
    from ..project import ScanProject

log = get_logger(__name__)

SCHEMA_ID = "aura-scan-metadata/2.0"

REQUIRED_KEYS = ("schema", "id", "capture_mode", "geometry_files")

_LOCATION_DEFAULTS = {
    "lat": None,
    "lng": None,
    "gps_accuracy_m": None,
    "local_frame": "",
    "datum": "GDA2020",
    "mga_zone": 56,
    "notes": "",
}

_GEOMETRY_DEFAULTS = {
    "lod1_glb": "",
    "lod1_obj": "",
    "lod2_obj": "",
    "lod2_ply": "",
    "pointcloud_ply": "",
    "pointcloud_las": "",
    "dxf": "",
    "thumbnail": "",
}


def _text(value, default: str = "") -> str:
    """Manifest string with a safe default; YAML nulls become the default."""
    if value is None or value == "":
        return default
    return value if isinstance(value, str) else str(value)


def build_metadata(
    project: ScanProject,
    cfg: dict,
    *,
    scale_block: dict,
    asset_report: AssetReport | None = None,
    plane_report: PlaneReport | None = None,
    geometry_files: dict,
    stage_stats: dict,
) -> dict:
    manifest = project.manifest or {}

    capture = manifest.get("capture") or {}
    capture_app = _text(capture.get("app")).strip() or "other"
    device = _text(capture.get("device")).strip() or "iPhone14ProMax"
    captured_at = _text(capture.get("captured_at"))

    location = dict(_LOCATION_DEFAULTS)
    manifest_location = manifest.get("location") or {}
    for key in location:
        value = manifest_location.get(key)
        if value is not None:
            location[key] = value

    manifest_links = manifest.get("links") or {}
    links = {key: manifest_links.get(key) for key in ("places_link_id", "sensorium_tile", "mind_palace")}

    manifest_perms = manifest.get("permissions") or {}
    permissions = {
        "captured_by": _text(manifest_perms.get("captured_by")),
        "consent_note": _text(manifest_perms.get("consent_note")),
    }

    dimensions: dict = {"length_m": None, "width_m": None, "height_m": None}
    if asset_report is not None:
        dims = asset_report.dims_m or {}
        for src, dst in (("length", "length_m"), ("width", "width_m"), ("height", "height_m")):
            if dims.get(src) is not None:
                dimensions[dst] = float(dims[src])

    projection: dict = {
        "is_projection_surface": False,
        "planarity_rmse_m": None,
        "normal": None,
        "usable_area_m2": None,
        "suitable": None,
        "material_notes": _text(manifest.get("material_notes")),
    }
    if plane_report is not None:
        projection.update({
            "is_projection_surface": True,
            "planarity_rmse_m": float(plane_report.rmse_m),
            "normal": [float(x) for x in np.asarray(plane_report.normal, dtype=float).reshape(3)],
            "usable_area_m2": float(plane_report.usable_area_m2),
            "suitable": bool(plane_report.suitable),
        })

    files = dict(_GEOMETRY_DEFAULTS)
    for key, value in (geometry_files or {}).items():
        if value:
            files[key] = str(value)

    if scale_block is None:
        scale_block = {"method": "arkit", "factor_applied": 1.0, "verified": False,
                       "constraints": [], "rmse_m": None, "max_error_m": None}

    meta = {
        "schema": SCHEMA_ID,
        "id": str(uuid.uuid4()),
        "name": project.name,
        "capture_mode": project.mode,
        "asset_type": _text(manifest.get("asset_type"), "other"),
        "condition": _text(manifest.get("condition")),
        "notes": _text(manifest.get("notes")),
        "captured_at": captured_at,
        "processed_at": now_iso(),
        "device": device,
        "capture_app": capture_app,
        "location": location,
        "frame": {
            "up_axis": "Z",
            "units": _text(cfg_get(cfg, "units.internal", "m"), "m"),
            # DESIGN.md: the manifest records origin notes under location.
            "origin_note": _text(cfg_get(manifest, "location.notes", "")),
        },
        "scale": scale_block,
        "dimensions": dimensions,
        "geometry_files": files,
        "projection": projection,
        "links": links,
        "sensorium_evidence": {
            "rung": "lidar",
            "status": "captured",
            "note": f"{capture_app} capture processed by aura-scan-pipeline v{__version__}",
        },
        "permissions": permissions,
        "pipeline": {
            "version": __version__,
            "config_mode": cfg.get("_mode") or project.mode,
            "stages": stage_stats or {},
        },
    }
    return np_to_list(meta)


def write_metadata(meta: dict, exports_dir: Path) -> Path:
    missing = [key for key in REQUIRED_KEYS if not meta.get(key)]
    if missing:
        raise PipelineError(
            "Metadata is missing required keys: " + ", ".join(missing)
            + ". Build the record with build_metadata() before writing."
        )
    exports_dir = ensure_dir(Path(exports_dir))
    path = exports_dir / "metadata.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(np_to_list(meta), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    log.info("Wrote metadata sidecar %s", path.name)
    return path
