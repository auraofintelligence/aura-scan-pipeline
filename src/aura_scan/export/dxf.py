"""Layered, dimensioned DXF export: plan footprint, sections and notes.

Internal geometry arrives in metres. The drawing is scaled to the unit
declared in ``dxf.units`` (millimetres by default, which reads best for
asset-sized captures) and ``$INSUNITS`` is set so CAD packages agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import ezdxf
import numpy as np

from ..util import PipelineError, cfg_get, ensure_dir, get_logger

if TYPE_CHECKING:
    from ..ops.asset import AssetReport
    from ..ops.plane import PlaneReport
    from ..ops.section import SectionResult

log = get_logger(__name__)

_INSUNITS = {"mm": 4, "m": 6}

_DEFAULT_LAYERS = {
    "existing": "EXISTING",
    "sections": "SECTIONS",
    "dimensions": "DIMENSIONS",
    "text": "TEXT",
    "hatch": "HATCH",
    "projection": "PROJECTION_SURFACE",
    "asset": "ASSET_OUTLINE",
}

# AutoCAD colour index per layer role.
_LAYER_COLOURS = {
    "existing": 8,      # grey
    "sections": 5,      # blue
    "dimensions": 1,    # red
    "text": 7,          # white on dark, black on light
    "hatch": 9,         # light grey
    "projection": 3,    # green
    "asset": 2,         # yellow
}


def _prepare_poly(poly: np.ndarray, scale: float) -> tuple[np.ndarray | None, bool]:
    """Scale a polyline to drawing units; report whether it closes on itself."""
    pts = np.asarray(poly, dtype=float)
    if pts.ndim != 2 or pts.shape[0] < 2 or pts.shape[1] < 2:
        return None, False
    pts = pts[:, :2] * scale
    if not np.all(np.isfinite(pts)):
        return None, False
    closed = pts.shape[0] >= 4 and bool(np.allclose(pts[0], pts[-1]))
    if closed:
        pts = pts[:-1]
    return pts, closed


def _bounds(arrays: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stacked = np.vstack(arrays)
    return stacked.min(axis=0), stacked.max(axis=0)


def _as_tuples(pts: np.ndarray) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in pts]


def export_dxf(
    path: Path,
    *,
    footprint_2d: np.ndarray | None,
    sections: list[SectionResult],
    asset_report: AssetReport | None = None,
    plane_report: PlaneReport | None = None,
    meta_lines: list[str],
    cfg: dict,
) -> Path:
    """Write the layered plan, sections, dimensions and notes drawing."""
    path = Path(path)
    units = str(cfg_get(cfg, "dxf.units", "mm")).lower()
    if units not in _INSUNITS:
        log.warning("Unknown dxf.units '%s'; falling back to mm", units)
        units = "mm"
    scale = 1000.0 if units == "mm" else 1.0

    version = str(cfg_get(cfg, "dxf.version", "R2018"))
    try:
        doc = ezdxf.new(dxfversion=version, setup=True)
    except ezdxf.DXFVersionError:
        log.warning("Unsupported dxf.version '%s'; using R2018", version)
        doc = ezdxf.new(dxfversion="R2018", setup=True)
    doc.header["$INSUNITS"] = _INSUNITS[units]
    doc.header["$MEASUREMENT"] = 1  # metric

    layer_cfg = cfg_get(cfg, "dxf.layers", {}) or {}
    layers = {**_DEFAULT_LAYERS, **{str(k): str(v) for k, v in layer_cfg.items()}}
    for role, name in layers.items():
        if name not in doc.layers:
            doc.layers.add(name, color=_LAYER_COLOURS.get(role, 7))
    msp = doc.modelspace()

    # Plan geometry at the origin.
    plan_polys: list[tuple[np.ndarray, str]] = []
    if footprint_2d is not None:
        footprint, _ = _prepare_poly(footprint_2d, scale)
        if footprint is not None and footprint.shape[0] >= 3:
            layer = layers["asset"] if asset_report is not None else layers["existing"]
            plan_polys.append((footprint, layer))
        else:
            log.warning("Footprint outline is degenerate; leaving it out of the DXF")
    if plane_report is not None:
        boundary, _ = _prepare_poly(plane_report.boundary_2d, scale)
        if boundary is not None and boundary.shape[0] >= 3:
            plan_polys.append((boundary, layers["projection"]))
        else:
            log.warning("Projection surface boundary is degenerate; leaving it out of the DXF")
    for pts, layer in plan_polys:
        msp.add_lwpolyline(_as_tuples(pts), close=True, dxfattribs={"layer": layer})

    if plan_polys:
        lo, hi = _bounds([p for p, _ in plan_polys])
        plan_min_x, plan_min_y = float(lo[0]), float(lo[1])
        plan_max_x, plan_max_y = float(hi[0]), float(hi[1])
    else:
        plan_min_x = plan_min_y = plan_max_x = plan_max_y = 0.0
    plan_w = plan_max_x - plan_min_x
    plan_h = plan_max_y - plan_min_y

    # Collect drawable section clusters before layout so sizing can use them.
    clusters: list[dict] = []
    for result in sections or []:
        entries: list[tuple[np.ndarray, bool]] = []
        for poly in result.polylines or []:
            pts, closed = _prepare_poly(poly, scale)
            if pts is not None:
                entries.append((pts, closed))
        if not entries:
            log.info("Section '%s' has no drawable polylines; skipped", result.spec.name)
            continue
        lo, hi = _bounds([p for p, _ in entries])
        clusters.append({"name": result.spec.name, "entries": entries, "lo": lo, "hi": hi})

    # Reference width drives text size and spacing; fall back when the plan is empty.
    ref_width = plan_w
    if ref_width <= 0.0 and clusters:
        ref_width = max(float(c["hi"][0] - c["lo"][0]) for c in clusters)
    if ref_width <= 0.0:
        ref_width = 1.0 * scale
    char_h = min(max(0.02 * ref_width, 0.0025 * scale), 1.0 * scale)
    dim_off = 3.0 * char_h
    gap = float(cfg_get(cfg, "dxf.section_gap_factor", 1.4)) * ref_width
    eps = 1e-3 * scale  # skip dimensions shorter than a millimetre equivalent

    dim_override = {
        "dimtxt": char_h,
        "dimasz": 0.8 * char_h,
        "dimexe": 0.4 * char_h,
        "dimexo": 0.25 * char_h,
        "dimgap": 0.25 * char_h,
        "dimdec": 0 if units == "mm" else 3,
    }

    def _linear_dim(base: tuple[float, float], p1: tuple[float, float],
                    p2: tuple[float, float], angle: float = 0.0) -> None:
        msp.add_linear_dim(
            base=base, p1=p1, p2=p2, angle=angle,
            dimstyle="EZDXF", override=dim_override,
            dxfattribs={"layer": layers["dimensions"]},
        ).render()

    # Overall plan dimensions: width below, depth to the left.
    if plan_polys:
        if plan_w > eps:
            _linear_dim((plan_min_x, plan_min_y - dim_off),
                        (plan_min_x, plan_min_y), (plan_max_x, plan_min_y))
        if plan_h > eps:
            _linear_dim((plan_min_x - dim_off, plan_min_y),
                        (plan_min_x, plan_min_y), (plan_min_x, plan_max_y), angle=90.0)

    # Sections in a row to the right of the plan, each with its own extents.
    cursor_x = (plan_max_x if plan_polys else 0.0) + gap
    base_y = plan_min_y
    for cluster in clusters:
        lo, hi = cluster["lo"], cluster["hi"]
        w = float(hi[0] - lo[0])
        h = float(hi[1] - lo[1])
        shift = np.array([cursor_x - float(lo[0]), base_y - float(lo[1])])
        for pts, closed in cluster["entries"]:
            msp.add_lwpolyline(_as_tuples(pts + shift), close=closed,
                               dxfattribs={"layer": layers["sections"]})
        msp.add_text(str(cluster["name"]), dxfattribs={
            "layer": layers["text"],
            "height": char_h,
            "insert": (cursor_x, base_y - 2.5 * char_h),
        })
        top, right = base_y + h, cursor_x + w
        if w > eps:
            _linear_dim((cursor_x, top + dim_off), (cursor_x, top), (right, top))
        if h > eps:
            _linear_dim((right + dim_off, base_y), (right, base_y), (right, top), angle=90.0)
        cursor_x = right + gap

    # Notes block above the plan. \P is the MTEXT line break.
    text = r"\P".join(str(line) for line in meta_lines or [])
    if not plan_polys and not clusters:
        note = "No plan or section geometry was generated for this scan."
        text = text + r"\P" + note if text else note
        log.warning("DXF has no geometry; writing the notes block only")
    if text:
        attribs: dict = {"layer": layers["text"], "char_height": char_h}
        if plan_polys:
            attribs["insert"] = (plan_min_x, plan_max_y + dim_off)
            attribs["attachment_point"] = 7  # bottom left: grows upward, clear of the plan
        else:
            attribs["insert"] = (0.0, 0.0)
            attribs["attachment_point"] = 1  # top left
        msp.add_mtext(text, dxfattribs=attribs)

    auditor = doc.audit()
    if auditor.errors:
        log.warning("DXF audit flagged %d issue(s); the file is still written",
                    len(auditor.errors))
    ensure_dir(path.parent)
    try:
        doc.saveas(path)
    except OSError as exc:
        raise PipelineError(f"Could not write DXF to {path}: {exc}") from exc
    log.info("Wrote %s (%s, %d section cluster(s))", path.name, units, len(clusters))
    return path
