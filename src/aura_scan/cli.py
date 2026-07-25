"""Command line interface: init, pick, run, report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .project import ScanProject, VALID_MODES
from .util import PipelineError, get_logger

log = get_logger("aura-scan")


def cmd_init(args) -> int:
    project = ScanProject.init(Path(args.parent), args.name, args.mode)
    print(f"Created {project.root}")
    print(f"1. Fill in {project.root / 'manifest.yaml'}")
    print(f"2. Drop phone exports into {project.raw_dir}")
    print(f"3. Optional: aura-scan pick {project.root}   (control points)")
    print(f"4. Run: aura-scan run {project.root}")
    return 0


def cmd_pick(args) -> int:
    import numpy as np
    import open3d as o3d

    from .config import load_config
    from .io.ingest import discover_inputs, load_input, to_pointcloud

    project = ScanProject(Path(args.project))
    cfg = load_config(project.mode, project.manifest.get("config") or {})
    paths = discover_inputs(project.raw_dir)
    if not paths:
        raise PipelineError(f"No inputs in {project.raw_dir} to pick points on.")
    pc = to_pointcloud(load_input(paths[0], cfg), cfg)
    print("A viewer window will open.")
    print("Shift + left click to pick points; shift + right click to undo; close the window to finish.")
    vis = o3d.visualization.VisualizerWithEditing()
    vis.create_window(window_name=f"aura-scan pick: {project.name}")
    vis.add_geometry(pc)
    vis.run()
    vis.destroy_window()
    indices = vis.get_picked_points()
    if not indices:
        print("No points picked; manifest unchanged.")
        return 0
    points = np.asarray(pc.points)
    control = project.manifest.setdefault("control", {})
    picked = control.setdefault("picked_points", [])
    start = len(picked)
    for idx in indices:
        picked.append([round(float(v), 5) for v in points[idx]])
    project.save_manifest()
    print(f"Added {len(indices)} point(s) as indices {start}..{len(picked) - 1}.")
    print("Now add tape measurements to control.distance_constraints in manifest.yaml, e.g.")
    print(f'  - {{points: [{start}, {start + 1}], distance_m: 1.520, note: "table top, long edge"}}')
    return 0


def cmd_run(args) -> int:
    from .pipeline import run_scan

    project = ScanProject(Path(args.project))
    summary = run_scan(project)
    dims = summary.get("dimensions_m") or {}
    print(f"\nScan '{summary['scan']}' processed ({summary['mode']}).")
    if dims:
        print(f"Oriented dimensions: {dims.get('length'):.3f} x {dims.get('width'):.3f}"
              f" x {dims.get('height'):.3f} m")
    print(f"Exports: {project.exports_dir}")
    return 0


def cmd_report(args) -> int:
    project = ScanProject(Path(args.project))
    summary_path = project.exports_dir / "run-summary.json"
    if not summary_path.exists():
        raise PipelineError(f"No run-summary.json in {project.exports_dir}; run the pipeline first.")
    with open(summary_path, "r", encoding="utf-8") as fh:
        print(json.dumps(json.load(fh), indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="aura-scan",
        description="iPhone LiDAR capture to CAD-ready digital twin assets, processed locally.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a new scan project folder")
    p_init.add_argument("name")
    p_init.add_argument("--mode", choices=VALID_MODES, default="general")
    p_init.add_argument("--parent", default="scans", help="parent folder (default: scans/)")
    p_init.set_defaults(func=cmd_init)

    p_pick = sub.add_parser("pick", help="pick control points on the first raw input")
    p_pick.add_argument("project")
    p_pick.set_defaults(func=cmd_pick)

    p_run = sub.add_parser("run", help="run the full pipeline on a scan project")
    p_run.add_argument("project")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="print the last run summary")
    p_report.add_argument("project")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
