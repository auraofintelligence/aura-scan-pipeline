"""DXF export: valid files, expected layers and entities, empty-input safety."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import numpy as np

from aura_scan.config import load_config
from aura_scan.export.dxf import export_dxf
from aura_scan.ops.section import SectionResult, SectionSpec


def _closed_rect(width: float, height: float) -> np.ndarray:
    return np.array([
        [0.0, 0.0], [width, 0.0], [width, height], [0.0, height], [0.0, 0.0],
    ])


def _fake_section(name: str = "cross-section") -> SectionResult:
    spec = SectionSpec(name=name, origin=np.zeros(3), normal=np.array([1.0, 0.0, 0.0]))
    return SectionResult(spec=spec, polylines=[_closed_rect(1.2, 0.8)],
                         extent_2d=(1.2, 0.8))


def test_export_dxf_footprint_and_sections(tmp_path: Path) -> None:
    cfg = load_config("asset")
    path = tmp_path / "asset.dxf"

    out = export_dxf(
        path,
        footprint_2d=_closed_rect(1.8, 0.85),
        sections=[_fake_section()],
        asset_report=None,
        plane_report=None,
        meta_lines=["Aura Scan test export", "local frame, metres internal, Z up"],
        cfg=cfg,
    )

    assert Path(out).exists()
    doc = ezdxf.readfile(str(path))
    names = {layer.dxf.name for layer in doc.layers}
    assert {"SECTIONS", "DIMENSIONS", "TEXT"} <= names
    # The footprint lands on ASSET_OUTLINE or EXISTING depending on mode handling.
    assert names & {"ASSET_OUTLINE", "EXISTING"}

    msp = doc.modelspace()
    assert len(msp.query("LWPOLYLINE")) >= 1
    assert len(msp.query("MTEXT")) >= 1
    assert doc.header.get("$INSUNITS") == 4  # asset mode exports in millimetres


def test_export_dxf_empty_geometry(tmp_path: Path) -> None:
    cfg = load_config("general")
    path = tmp_path / "empty.dxf"

    out = export_dxf(
        path,
        footprint_2d=np.zeros((0, 2)),
        sections=[],
        asset_report=None,
        plane_report=None,
        meta_lines=["empty geometry case"],
        cfg=cfg,
    )

    assert Path(out).exists()
    doc = ezdxf.readfile(str(path))
    # Even with nothing to draw, the metadata text block must be present.
    assert len(doc.modelspace().query("MTEXT")) >= 1
