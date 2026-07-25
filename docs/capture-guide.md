# Capture guide

Field guide for scanning with an iPhone and getting the files onto the Windows box. The technique here is standard LiDAR practice written down for this pipeline; no real captures have been run through it yet, so treat it as the starting recipe and improve it as field experience comes in.

## The capture apps

Five iPhone apps cover the today lane. All of them use the same LiDAR sensor; they differ in what they let you export and what they charge for it.

| App | Free | Paid | Best export for this pipeline |
| --- | --- | --- | --- |
| **Polycam** | Capture and in-app viewing; export options on the free tier have narrowed over time | Subscription unlocks the useful raw exports (OBJ, PLY, LAS, point cloud, DXF) | OBJ or PLY if your tier allows, otherwise GLB |
| **Scaniverse** | Capture and, historically, all exports free (OBJ, GLB, USDZ, STL, PLY, LAS) | Nothing needed so far | PLY point cloud plus an OBJ or GLB mesh |
| **3d Scanner App** (Laan Labs) | Capture and mesh exports free (OBJ, GLB, USDZ, STL, PLY, point cloud) plus an "All Data" raw dump | Some processing extras | OBJ or PLY. The "All Data" package is not a raw layout the pipeline recognises yet |
| **Stray Scanner** | Records raw depth frames, camera poses and RGB video; no meshing in the app | Small one-off price historically | The whole capture folder, unchanged. This is exactly the raw package the pipeline fuses natively |
| **Record3D** | RGBD video capture with limited export | Unlocks per-frame exports and USB streaming | Only if you already use it: export PLY, or repack frames to the generic raw spec in [formats.md](formats.md) |

Honest note: pricing tiers and export menus change often and without notice. Verify in the app before a capture trip, and do not buy a subscription on the strength of this table.

**Which format, in one line.** A mesh (OBJ or GLB) plus a point cloud (PLY or LAS) when the app offers both; the pipeline prefers the app's own mesh and always writes point clouds out. USDZ works only with the optional `usd` extra installed, so avoid it when another format is on offer. The full list the pipeline reads: OBJ, GLB, GLTF, STL, DAE, PLY, PCD, XYZ, LAS, LAZ, USDZ, and raw depth packages (Stray Scanner's layout, or the generic spec in [formats.md](formats.md)).

## Technique

The sensor is honest but short-sighted. It reads best between 1 and 3 metres, and the phone stitches your movement into one model as you go, so how you move is most of the result.

- **Establish the floor first.** Start with a slow sweep of the ground around the subject before you lift the phone to it. The floor gives the tracker a stable anchor.
- **Slow arcs.** Move like you are painting the surface with a wide slow brush, not spraying it. Fast swings blur the tracking.
- **Stay 1 to 3 metres away.** Closer than 1 m the depth gets noisy; past 3 m it thins out fast.
- **Overlap every pass.** Each new arc should re-cover about a third of the previous one. Overlap is what lets separate passes lock together.
- **Close the loop.** Finish where you started, looking at your starting view. Coming back to a place the phone has already seen lets it pull accumulated drift back out of the model.
- **Avoid glass, mirrors, water and wet surfaces.** The laser goes through glass and skates off water, leaving holes and phantom geometry. After rain, wait for surfaces to dry.
- **Scan in early morning light for texture.** Soft even light means the colour photos draped over the model carry no harsh baked-in shadows. Midday sun also heats the phone until it throttles.
- **Watch the live preview.** Every app shows the mesh growing. If you see a hole, fill it on the spot; it is a ten second fix in the field and an unfixable gap at the desk.

### Per mode

- **Asset** (picnic table, bbq, bubbler): orbit the whole object in overlapping rings at two heights, then capture all sides plus the underside where it is safe to kneel and reach. Undersides are where scans usually fail an inventory check.
- **Projection surface** (wall, screen): even side-to-side passes across the whole face, constant distance, then deliberate extra attention to all four corners. Corners define the usable area.
- **General** (structures, bowls, mixed scenes): break the subject into overlapping passes rather than one marathon capture, and close the loop on each pass.

## Control measurements

The phone's scale is usually within about one per cent, and the pipeline checks it rather than trusting it. Over a 3 metre span, one per cent is 30 mm, about two finger widths: enough to matter in a drawing. The check needs real tape measurements from the site.

At the site:

1. Pick recognisable hard corners: table ends, slab corners, coping edges. Points you will be able to find again on a screen.
2. Tape-measure **two or more** distances between those corners, **longest span first**. Long spans expose scale error; short ones hide it in noise.
3. Write the distances down at the site: notes app, or a photo of the tape in place. Do not trust memory on the ferry home.

Back on the PC:

```powershell
aura-scan pick scans\my-scan
```

A viewer window opens on the first raw input (name your main pass so it sorts first, for example `01-main.ply`). Shift + left click the same corners in the same order you taped them, then close the window. The points land in `manifest.yaml`. Add the tape numbers against them:

```yaml
control:
  distance_constraints:
    - {points: [0, 1], distance_m: 3.640, note: "coping to coping, longest span"}
    - {points: [2, 3], distance_m: 1.685, note: "table top, long edge"}
```

On the next `aura-scan run`, the pipeline compares scan distances against the tape, reports the error per constraint, and only rescales the geometry when the correction is bigger than the configured tolerance. The result is written into the metadata and stamped into the DXF text block either way, so a reader downstream can see exactly how much to trust the dimensions.

## Getting files to the Windows box

| Route | Needs | Notes |
| --- | --- | --- |
| **LocalSend** (recommended) | Both devices on the same wifi | Free, open source, works fully offline, no account. Install from the Microsoft Store or localsend.org, share from the capture app to LocalSend on the phone. |
| **USB cable** | Apple Devices app on Windows 11 (iTunes file sharing on older setups) | Reliable for big raw packages. The capture app must expose its documents to file sharing. |
| **iCloud for Windows** | Internet and iCloud storage space | Save the export to iCloud Drive on the phone, let it sync down. Slowest and the only route that touches a cloud. |

Whatever the route, the file ends its journey in `scans\<name>\raw\`.

## Checklists

**Every capture**

- [ ] Establish the floor first
- [ ] Slow arcs, 1 to 3 m, overlapping passes
- [ ] Close the loop back to the starting view
- [ ] Tape two or more control distances, longest span first, written down
- [ ] Note the capture app and date for the manifest
- [ ] Give the scan's local frame a short name (for example `windemere-bowl-1`)

**Asset mode extras**

- [ ] All sides, plus the underside where safe
- [ ] A straight condition photo for the inventory (chips, rust, missing bits)
- [ ] Asset type noted (picnic_table, bbq, bubbler, ...)

**Projection surface extras**

- [ ] Even coverage across the whole face, constant distance
- [ ] Extra passes on all four corners
- [ ] Note the material and how reflective it is (matt render, gloss paint, metal)
