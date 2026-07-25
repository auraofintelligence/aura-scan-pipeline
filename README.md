# Aura Scan Pipeline

Phone scans in, measurable CAD-ready twins out, processed entirely on your own machine. You walk around a picnic table, a wall or a skate bowl with an iPhone, export the scan from a LiDAR app (LiDAR is the phone's laser depth sensor: it measures distance with light the way a bat does with sound), drop the file onto a Windows PC, and one command turns it into web-ready 3D models, a point cloud and a measured, layered CAD drawing. No cloud services, no accounts, no uploads. The data never leaves your own gear.

## Where this is up to

Plain status, no spin:

- The PC pipeline is built and runs end to end, proven on synthetic test shapes and on the export formats the capture apps produce.
- No real-world capture has been processed yet. First field scans are the next step.
- The custom iPhone capture app is unbuilt source in [`ios/`](ios/README.md). Compiling it needs Xcode on a Mac, and this project runs on a Windows box, so today's captures come from existing apps.

## Who it serves

- **Places catalogue assets.** Picnic tables, bubblers, bbqs and halls become inventory rows with real dimensions and a web model.
- **Mind palace objects.** Low-poly meshes light enough to load in a browser scene.
- **Projection art surfaces.** Walls and screens checked for flatness, with a usable-area outline for mapping light onto them.
- **The Windemere bowl tender.** Existing-conditions drawings for a skate bowl refurbishment bid: see [docs/windemere-workflow.md](docs/windemere-workflow.md).

## Quickstart

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv` if you don't have it). From the repo root:

```powershell
uv venv
uv pip install -e ".[dev]"
.venv\Scripts\activate
```

Worked example, a picnic table:

```powershell
# 1. Make a scan project (creates scans\picnic-table-one-mile\)
aura-scan init picnic-table-one-mile --mode asset

# 2. Scan the table with a phone app and drop the export
#    (OBJ, PLY, GLB, LAS or a raw depth package) into
#    scans\picnic-table-one-mile\raw\

# 3. Optional but recommended: click the corners you tape-measured on site
aura-scan pick scans\picnic-table-one-mile
```

Then open `scans\picnic-table-one-mile\manifest.yaml` and add the tape measurements against the picked points:

```yaml
control:
  picked_points:            # written by aura-scan pick
    - [0.412, -0.788, 0.731]
    - [2.109, -0.815, 0.742]
  distance_constraints:
    - {points: [0, 1], distance_m: 1.685, note: "table top, long edge"}
```

```powershell
# 4. Run the full pipeline
aura-scan run scans\picnic-table-one-mile

# 5. Re-read any processed scan later
aura-scan report scans\picnic-table-one-mile
```

Everything lands in `scans\picnic-table-one-mile\exports\`: meshes, point clouds, a dimensioned DXF drawing, a `metadata.json` sidecar, a thumbnail and a web viewer snippet.

## Three capture modes

| Mode | For | What it adds |
| --- | --- | --- |
| `general` | Any geometry | Balanced defaults, automatic plan and cross sections |
| `asset` | Public assets (picnic tables, toilets, bbqs, halls, skate features) | Oriented dimensions, footprint outline, condition field, inventory-ready simplification |
| `projection_surface` | Flat or near-flat surfaces for projection art | Plane fit, flatness check, usable-area outline, a plain suitable / not suitable verdict |

## The LOD ladder

One capture, four tiers of detail, produced in a single run. LOD means level of detail: the same object at different weights for different jobs.

| Tier | What you get | Good for |
| --- | --- | --- |
| **LOD0** | Oriented bounding box and 2D footprint | A map pin and an inventory row |
| **LOD1** | Low-poly web mesh (GLB, under 15k triangles) | Mind palace objects, browser model viewers, the places catalogue |
| **LOD2** | Full-detail cleaned mesh (OBJ, PLY) | The archival geometry |
| **LOD3** | Engineering pack: point cloud (PLY, LAS), dimensioned DXF plan and sections, flatness and dimension reports | AutoCAD-compatible work and tenders |

## Two capture lanes

- **Capture today.** Use an existing free iPhone LiDAR app (Polycam, Scaniverse, 3d Scanner App, Stray Scanner, Record3D) and drop its export into `raw/`. This lane works now; [docs/capture-guide.md](docs/capture-guide.md) covers the apps, field technique and getting files onto the PC.
- **Custom app later.** Full SwiftUI and ARKit source for a purpose-built capture app lives in [`ios/`](ios/README.md). It has never been compiled, because that takes Xcode on a Mac. When it is built, its raw depth output becomes the preferred lane. Nothing downstream cares which lane captured the data.

## More to read

- [DESIGN.md](DESIGN.md): the full design, scale policy, metadata schema and phases.
- [INTERFACES.md](INTERFACES.md): the module contracts, for anyone working on the code.
- [docs/capture-guide.md](docs/capture-guide.md): field guide for scanning and transfer.
- [docs/windemere-workflow.md](docs/windemere-workflow.md): the worked engineering lane, scan to tender drawing.
- [configs/](configs/): every threshold and layer name, in plain YAML.

---

🤝🔷 A Luke × Claude build. [Public Source Licence](LICENCE.md).

**Luke Hayes × Claude**, Minjerribah · 2026.
