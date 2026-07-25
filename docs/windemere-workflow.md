# Windemere bowl workflow

The worked engineering lane: from phone scans of a skate bowl to existing-conditions drawings that can sit inside a tender submission.

The target is real. Redland City Council tender PDG-20842-1 asks for design-and-construct refurbishment of the skate bowl at Windemere Road Park, Alexandra Hills, and a public exploratory workspace for it exists as the `windemere-skate-bowl-tender-workspace` repo (a Strange But True x VFG collaboration space).

**Status, plainly.** Every step below runs today on synthetic fixtures and on capture-app exports. The bowl itself has not been scanned yet; the workspace records that the mandatory site inspection on 15 July 2026 was covered with ten geotagged GoPro 360 captures, not LiDAR. The tender workspace is exploratory, its decision gate (bid / no-bid) is open, and everything this pipeline produces for it stays concept-only and not for construction.

## Where this slots into the tender workspace

The workspace already names the gap this pipeline fills:

- Its **CAD workflow** page (`pages/cad-workflow.html`) lists "Existing bowl geometry" and "Survey, levels and services" under inputs needed from site, and puts "Existing conditions / opportunity plan" first among the drawing outputs.
- Its **cad/titleblock-notes.md** suggests the filename `PDG20842_Windemere_Concept_ExistingConditions_v01.dwg` for exactly that drawing.
- Its **cad/layers.md** proposes a layer standard with `EXST_BOWL` for existing bowl geometry.
- Its **cad/** folder holds a concept seed DXF (`PDG20842_Windemere_Concept_Seed_NotForConstruction.dxf`) waiting for real geometry to sit under it.
- Its **site evidence** pages (`pages/site-visit-360.html`, `pages/site-photos.html`) show the current state of the bowl, including the water pooling visible in the Appendix D tender photos. Useful twice: for planning where to scan, and as a warning to scan when the bowl is dry, because water breaks LiDAR.

A phone scan run through this pipeline feeds that first drawing: measured existing conditions of the bowl, with the scale honesty block attached.

## Scanning the bowl

A bowl is too big and too curved for one pass. Plan overlapping passes, per the technique in [capture-guide.md](capture-guide.md):

1. **Rim pass first.** Walk the full rim, phone angled down into the bowl, and close the loop back to your start. Name this export so it sorts first (for example `01-rim.ply`): the pipeline registers every other pass onto the first one, and control points are picked on it.
2. **Floor passes.** Walk the bowl floor in overlapping strips, phone 1 to 3 m from the concrete.
3. **Transition passes.** Slow arcs across each curved transition between floor and wall, with generous overlap into both the rim and floor passes.
4. **Control tape.** Tape-measure at least two long spans between recognisable coping edges, longest first (coping is the rounded lip at the top of the bowl wall), plus one shorter check distance. Write them down at the site.
5. **Conditions.** Dry concrete, early morning light. If the pooling from the tender photos is present, come back another day.

Drop all the exports into the one `raw/` folder; the pipeline registers and merges them into a single cloud.

## Project setup

```powershell
aura-scan init windemere-bowl --mode general
# copy the phone exports into scans\windemere-bowl\raw\
aura-scan pick scans\windemere-bowl
# add the tape spans to manifest.yaml, then:
aura-scan run scans\windemere-bowl
```

Mode `general` fits site work. Two manifest choices matter for a bowl: drawing units in metres (the default is millimetres, which suits small assets, not a site), and extra section planes through the transitions.

### Worked manifest

`scans\windemere-bowl\manifest.yaml`, the parts that differ from the template:

```yaml
name: "windemere-bowl"
mode: general
notes: "Existing conditions capture of the bowl only, concept support"

location:
  local_frame: windemere-bowl-1
  notes: "Origin at shallow-end coping, X along the bowl's long axis"

control:
  picked_points: []           # written by aura-scan pick
  distance_constraints: []    # add the tape spans after picking

sections:
  # Three vertical planes through the bowl, on top of the automatic
  # plan and principal cross sections. Origins are metres in this
  # scan's own frame; the normal is the direction the plane faces,
  # so a horizontal normal gives a vertical cut.
  extra:
    - {name: transition-shallow, origin: [1.5, 0.0, 0.0], normal: [1.0, 0.0, 0.0]}
    - {name: bowl-spine,         origin: [0.0, 0.0, 0.0], normal: [0.0, 1.0, 0.0]}
    - {name: transition-deep,    origin: [5.5, 0.0, 0.0], normal: [1.0, 0.0, 0.0]}

config:
  dxf:
    units: m
    layers:
      existing: EXST_BOWL   # match the workspace's cad/layers.md standard
```

The automatic sections already give a horizontal plan cut and two vertical cuts through the middle of the bounding box. The three extras aim where a refurbishment reader actually looks: a cut through the shallow-end transition, one along the spine, one through the deep-end transition, because the transitions are where curvature, cracking and repair scope live. Read the origin coordinates off the picked control points or a first run's plan, then adjust and re-run; runs are cheap.

## What lands in the DXF

`scans\windemere-bowl\exports\windemere-bowl.dxf`, readable by AutoCAD, LibreCAD, FreeCAD and friends:

- **Plan** of the bowl footprint at the drawing origin, on the `EXST_BOWL` layer (per the override above).
- **Sections** laid out in a row to the right on the `SECTIONS` layer, one per cutting plane, named: the automatic plan and cross sections plus `transition-shallow`, `bowl-spine` and `transition-deep`.
- **Dimensions** on the `DIMENSIONS` layer: overall extents for the plan and for every section.
- **Text block** on the `TEXT` layer: pipeline version, scan name and mode, local frame with the units and Z-up note, and the scale verification (method, factor, whether it was verified against tape). This block is the honesty seal: a reader can see in one glance whether the dimensions were checked. The full per-tape errors sit alongside in `metadata.json` and `run-summary.json`.
- `$INSUNITS` set to metres, so CAD software opens it at true size instead of guessing.

## DXF to DWG

Tenders and councils usually ask for DWG, which is a closed format this pipeline deliberately does not write natively. Two conversion routes:

- `python tools\dxf2dwg.py scans\windemere-bowl\exports\windemere-bowl.dxf`, which hands the file to ODA File Converter when it is installed (free download from the Open Design Alliance).
- FreeCAD, which opens the DXF directly and converts to DWG once it is pointed at that same ODA converter.

Name the result per the workspace convention: `PDG20842_Windemere_Concept_ExistingConditions_v01.dwg`.

## Into the tender submission

How the outputs slot in as existing-conditions material:

- The DWG becomes the base of the "Existing conditions / opportunity plan" from the CAD workflow page's output list, and real measured geometry under the concept options sketched on the workspace's concept design page.
- Every sheet carries the labels from the workspace's `cad/README.md`: Tender Concept Only, Not For Construction, Subject to Survey, Subject to RPEQ Certification (RPEQ is a Registered Professional Engineer of Queensland, the sign-off the real design will need).
- The scale verification text block stays on the sheet. It tells an assessing engineer what the dimensions are worth.
- Boundaries hold, as the workspace's role-boundaries and risk pages insist: a phone scan is not a licensed survey. It does not locate services, prove levels against a datum, or certify anything. What it does is give the team honest measured geometry early and cheaply, and flag where the proper survey needs to look.
- The workspace's public/private caution applies to scan outputs too: exports made for a real bid belong in the private working area, not the public Pages repo.

## Recap

Supported today: multi-pass merge, tape-checked scale, extra section planes, metre-unit layered DXF with dimensions and the verification block, DWG conversion via external tools. Not yet real: the bowl scan itself. When the tender team wants it, the recipe above is ready to walk.
