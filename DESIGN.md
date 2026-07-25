# Aura Scan Pipeline: Design

Self-sovereign 3D capture and processing for digital twin pockets on Quandamooka Country / Minjerribah. Phone captures the world; the Ryzen box does the heavy work; outputs feed the places catalogue, mind palaces, the sensorium tiles and engineering workflows like the Windemere skate bowl tender.

This document supersedes the July 2026 external handoff brief. The bones of that brief are kept; the changes below are grounded in what is actually on this machine and in the live repos.

## What changed from the original brief, and why

1. **Capture starts this week, not after an app build.** The original brief leads with a custom iPhone app, but iOS apps can only be compiled with Xcode on a Mac, and this is a Windows box. So the pipeline's front door is format-based, not app-based: it ingests exports from existing free iPhone LiDAR apps (Polycam, Scaniverse, 3d Scanner App, Stray Scanner, Record3D). The custom SwiftUI capture app is still in scope and its full source lives in `ios/`, ready for whenever Mac access happens. Nothing downstream cares which app captured the data.
2. **Scale is verified, not invented.** ARKit LiDAR output is already metric and usually within about one percent. Control measurements are therefore a verification and correction layer: you tape-measure one or two real distances, the pipeline compares them against the scan, reports the error, and only rescales when the constraint solver says so. Every export carries a scale verification block so an engineering reader can trust or reject it at a glance.
3. **A LOD ladder bridges hobby and engineering.** One capture, four tiers, produced in a single run:
   - **LOD0**: oriented bounding box and 2D footprint. Enough for a map pin and an inventory row.
   - **LOD1**: low-poly web mesh (GLB, target under 15k triangles). For mind palace objects, model viewers, the places catalogue.
   - **LOD2**: full-detail cleaned mesh (OBJ/PLY). The archival geometry.
   - **LOD3**: engineering pack: point cloud (PLY/LAS), dimensioned DXF plan and sections, planarity and dimension reports. For AutoCAD-compatible work and tenders.
4. **Outputs link into the ecosystem that already exists.**
   - **Events engine**: places are keyed by an id slug such as `public-point-lookout-skate-park` (in `assets/place-data-extra.js`). There is no assets field on a place yet, so the pipeline writes a `places-entry.json` proposing exactly the record to attach when the engine grows a `models` field, joined on the place id.
   - **Web3 Sensorium**: tiles already model capture evidence as `{rung, status, note}` with rungs `photo, photogrammetry, lidar, drone, open-data, modelled`. The metadata sidecar mirrors that vocabulary verbatim, so a scan flips a tile's lidar rung from `not-yet` to `captured` by copy-paste.
   - **Mind palaces**: no data schema exists yet on the site; LOD1 GLB objects plus their sidecars are the first real feedstock and define the object format by example.
   - **Windemere**: the bowl workflow (multiple vertical sections through transitions, dimensioned DXF) is documented in `docs/windemere-workflow.md` against the actual tender workspace repo.
5. **Coordinate honesty.** Internal units are metres, Z up. Every scan lives in its own named local frame; the manifest records origin notes. Phone GPS (plus or minus 3 to 5 metres) places the pin on the island map but is never used to merge geometry. Optional georeferencing targets GDA2020 / MGA zone 56 (the correct datum and zone for Minjerribah) and is a later phase.
6. **Proven before the first real scan.** Synthetic fixtures (a noisy picnic table, a tilted noisy wall, known-scale point pairs) drive an end-to-end test suite, so the pipeline is demonstrated working before any field capture.
7. **GPU honesty.** The RTX 2080 Ti is not required. Open3D's Windows wheels are CPU builds and asset-scale scans process fine on the Ryzen 9. Neural capture lanes (Gaussian splatting, photogrammetry via COLMAP) are a documented horizon, not a v1 dependency.

## Architecture

```
iPhone 14 Pro Max
├── Today lane: Polycam / Scaniverse / 3d Scanner App / Stray Scanner / Record3D
│     exports: OBJ, PLY, GLB, LAS, or raw depth+poses packages
├── Later lane: ios/ AuraScanCapture (SwiftUI + ARKit, built on a Mac)
└── Transfer: USB cable, iCloud folder, or LocalSend (offline wifi)
        │
        ▼  drop files into scans/<name>/raw/
Ryzen 9 PC  (Python 3.12, uv-managed venv)
├── aura-scan init     make a scan project + manifest
├── aura-scan pick     click control points on the cloud
├── aura-scan run      the full pipeline:
│   ingest → clean → register/merge → scale verify → mesh → repair
│   → LOD ladder → asset dims / plane fit → sections → exports
└── aura-scan report   summary of any processed scan
        │
        ▼  scans/<name>/exports/
DXF (plan + sections, layered, dimensioned) · OBJ/GLB/PLY/STL · PLY/LAS/PCD
metadata JSON sidecar · places-entry.json · thumbnail · viewer snippet
```

## Capture modes

- **general**: any geometry, balanced defaults.
- **asset**: public assets (picnic_table, toilet, bbq, bubbler, hall, library, club_asset, skate_feature, other). Adds oriented dimensions, footprint outline, inventory-ready simplification, condition field.
- **projection_surface**: flat or near-flat surfaces for light projection art. Adds RANSAC plane fit, planarity RMSE, surface normal, usable-area outline in the DXF, suitability verdict against a configurable RMSE threshold.

## Scale policy

1. ARKit metric scale is trusted as the starting point.
2. If the manifest carries distance constraints (pairs of picked points plus a tape-measured real distance), the solver computes the best uniform scale factor and per-constraint residuals.
3. The factor is applied only when it moves geometry by more than the configured tolerance; either way the verification block (factor, per-constraint error before and after, RMSE) is written into the metadata and stamped into the DXF text block.
4. Field protocol, marker sheets and picking instructions live in `docs/capture-guide.md`.

## Outputs (every run)

- LOD1 GLB and OBJ, LOD2 OBJ/PLY, LOD0 box + footprint
- Point cloud PLY (always) and LAS (when writable)
- Layered DXF: `EXISTING`, `SECTIONS`, `DIMENSIONS`, `TEXT`, `ASSET_OUTLINE`, `PROJECTION_SURFACE` (when relevant); `$INSUNITS` set; a text block recording origin, units, date, capture app and scale verification
- `metadata.json` sidecar (schema below)
- `places-entry.json` + `viewer-snippet.html` + `thumbnail.png` for the web
- DWG: convert the DXF with ODA File Converter or FreeCAD; a helper shells out to `ODAFileConverter.exe` when installed. Native DWG writing is deliberately out of scope.

## Metadata sidecar (schema 2.0)

```json
{
  "schema": "aura-scan-metadata/2.0",
  "id": "uuid4",
  "name": "",
  "capture_mode": "general | asset | projection_surface",
  "asset_type": "picnic_table | toilet | bbq | bubbler | hall | library | club_asset | skate_feature | projection_surface | other",
  "condition": "",
  "notes": "",
  "captured_at": "ISO-8601",
  "processed_at": "ISO-8601",
  "device": "iPhone14ProMax",
  "capture_app": "polycam | scaniverse | 3d-scanner-app | stray-scanner | record3d | aura-scan-capture | other",
  "location": {"lat": null, "lng": null, "gps_accuracy_m": null, "local_frame": "", "datum": "GDA2020", "mga_zone": 56, "notes": ""},
  "frame": {"up_axis": "Z", "units": "m", "origin_note": ""},
  "scale": {"method": "arkit | control_points", "factor_applied": 1.0, "verified": false,
            "constraints": [{"distance_m": 0.0, "measured_m": 0.0, "error_m": 0.0, "note": ""}],
            "rmse_m": null, "max_error_m": null},
  "dimensions": {"length_m": null, "width_m": null, "height_m": null},
  "geometry_files": {"lod1_glb": "", "lod1_obj": "", "lod2_obj": "", "lod2_ply": "",
                      "pointcloud_ply": "", "pointcloud_las": "", "dxf": "", "thumbnail": ""},
  "projection": {"is_projection_surface": false, "planarity_rmse_m": null, "normal": null,
                  "usable_area_m2": null, "suitable": null, "material_notes": ""},
  "links": {"places_link_id": null, "sensorium_tile": null, "mind_palace": null},
  "sensorium_evidence": {"rung": "lidar", "status": "captured", "note": ""},
  "permissions": {"captured_by": "", "consent_note": ""},
  "pipeline": {"version": "0.1.0", "config_mode": "asset", "stages": {}}
}
```

## Implementation phases

- **Phase 1 (this build)**: PC pipeline end-to-end on synthetic fixtures plus any real export dropped into `raw/`: ingest, clean, merge, scale verify, mesh, LOD ladder, asset dims, plane fit, sections, DXF, metadata, web bundle. CLI with `init / pick / run / report`.
- **Phase 2**: first real field captures; tune presets against real picnic tables, the Point Lookout skate park, a projection wall; extend the events engine with a `models` field.
- **Phase 3**: custom iOS capture app built on a Mac; raw depth+poses becomes the preferred lane; multi-scan registration hardening.
- **Phase 4**: island frame (GDA2020/MGA56 anchors), batch inventory runs, Windemere tender pack polish, splat/photogrammetry lanes.

## Constraints

- Open source, locally runnable, offline-first. No cloud services.
- Internal units metres; DXF units configurable per mode (millimetres for assets, metres for site work).
- All thresholds, layer names and presets in `configs/*.yaml`, deep-merged: default, then mode, then per-scan manifest overrides.
- Every export documents origin, units and coordinate frame.
