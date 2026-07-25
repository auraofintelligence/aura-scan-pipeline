"""Scan project folders: manifest, raw inputs, working files, exports."""

from __future__ import annotations

from pathlib import Path

import yaml

from .util import PipelineError, ensure_dir

MANIFEST_NAME = "manifest.yaml"

MANIFEST_TEMPLATE = """\
# Aura Scan project manifest. Fill in what you know; everything is optional
# except mode. Distances in metres.
name: "{name}"
mode: {mode}            # general | asset | projection_surface
asset_type: other       # picnic_table | toilet | bbq | bubbler | hall | library | club_asset | skate_feature | projection_surface | other
condition: ""           # quick condition note for the asset inventory
notes: ""

capture:
  app: ""               # polycam | scaniverse | 3d-scanner-app | stray-scanner | record3d | aura-scan-capture | other
  device: iPhone14ProMax
  captured_at: ""       # ISO date, e.g. 2026-07-24

location:
  lat: null
  lng: null
  gps_accuracy_m: null
  local_frame: ""       # short name for this scan's coordinate frame, e.g. windemere-bowl-1
  notes: ""

links:
  places_link_id: null  # events engine place id, e.g. public-point-lookout-skate-park
  sensorium_tile: null  # web3-sensorium tile id, if any
  mind_palace: null     # palace or room this object belongs to, if any

permissions:
  captured_by: ""
  consent_note: ""      # who okayed the capture; cultural or private-land notes

control:
  # Points picked on the PC with:  aura-scan pick <project>
  # Each entry is [x, y, z] in the scan's own frame. Indices start at 0.
  picked_points: []
  # Tape-measured real distances between picked points.
  # Example: - {{points: [0, 1], distance_m: 1.520, note: "table top, long edge"}}
  distance_constraints: []

sections:
  # Extra cutting planes on top of the automatic plan and cross sections.
  # Example: - {{name: bowl-centre, origin: [0, 0, 0.5], normal: [1, 0, 0]}}
  extra: []

config: {{}}            # overrides of configs/default.yaml keys, deep-merged last
"""

VALID_MODES = ("general", "asset", "projection_surface")


class ScanProject:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        manifest_path = self.root / MANIFEST_NAME
        if not manifest_path.exists():
            raise PipelineError(
                f"No {MANIFEST_NAME} in {self.root}. Create a project with: aura-scan init <name>"
            )
        with open(manifest_path, "r", encoding="utf-8") as fh:
            self.manifest = yaml.safe_load(fh) or {}
        self.raw_dir = ensure_dir(self.root / "raw")
        self.work_dir = ensure_dir(self.root / "work")
        self.exports_dir = ensure_dir(self.root / "exports")

    @property
    def name(self) -> str:
        return self.manifest.get("name") or self.root.name

    @property
    def mode(self) -> str:
        mode = self.manifest.get("mode", "general")
        if mode not in VALID_MODES:
            raise PipelineError(f"Unknown mode '{mode}' in manifest; expected one of {VALID_MODES}")
        return mode

    @classmethod
    def init(cls, parent: Path, name: str, mode: str = "general") -> "ScanProject":
        if mode not in VALID_MODES:
            raise PipelineError(f"Unknown mode '{mode}'; expected one of {VALID_MODES}")
        root = ensure_dir(Path(parent) / name)
        manifest_path = root / MANIFEST_NAME
        if manifest_path.exists():
            raise PipelineError(f"{manifest_path} already exists")
        manifest_path.write_text(MANIFEST_TEMPLATE.format(name=name, mode=mode), encoding="utf-8")
        for sub in ("raw", "work", "exports"):
            ensure_dir(root / sub)
        return cls(root)

    def save_manifest(self) -> None:
        with open(self.root / MANIFEST_NAME, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.manifest, fh, sort_keys=False, allow_unicode=True)

    def picked_points(self) -> list:
        control = self.manifest.get("control") or {}
        return control.get("picked_points") or []

    def distance_constraint_dicts(self) -> list:
        control = self.manifest.get("control") or {}
        return control.get("distance_constraints") or []

    def extra_section_dicts(self) -> list:
        sections = self.manifest.get("sections") or {}
        return sections.get("extra") or []
