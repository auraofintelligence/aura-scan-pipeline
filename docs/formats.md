# Input formats

How captures get from the phone into `scans/<name>/raw/`, and exactly what the pipeline does with each thing it finds there. The parsers live in `src/aura_scan/io/ingest.py` and `src/aura_scan/io/raw_rgbd.py`; if this page and the code ever disagree, the code wins and this page has a bug.

Honesty first: everything below is written against each app's documented export layout and proven against synthetic fixtures, not yet against a fresh real export from every app. App developers move export options in and out of paywalls without notice, so treat the first real capture with each app as a test of this page, and correct it when it loses.

## Quick reference

| Input dropped in `raw/` | Read as | Notes |
|---|---|---|
| `.obj`, `.glb`, `.gltf`, `.stl`, `.dae` | mesh | via trimesh; multi-part scenes concatenated; `.dae` needs pycollada |
| `.ply` | mesh or point cloud | mesh when the file has faces, otherwise point cloud |
| `.pcd`, `.xyz` | point cloud | |
| `.las`, `.laz` | point cloud | via laspy; `.laz` also needs `pip install lazrs` |
| `.usdz` | mesh | needs `pip install usd-core`; without it, export OBJ or PLY instead |
| folder or `.zip` | raw depth package | Stray Scanner layout or the generic spec below |
| `.mtl`, `.png`, `.jpg`, `.jpeg`, `.bin`, hidden files | skipped | texture and buffer companions that ride along with mesh exports |

Anything else is skipped with a log line. A `.zip` that is not a raw depth package is also skipped: extract zipped mesh exports into `raw/` yourself.

## What each iPhone app can export, and what this pipeline reads

### Polycam

LiDAR and photogrammetry capture with a long export menu: GLB/glTF, OBJ, FBX, STL, PLY, USDZ, DXF, LAS, XYZ and more. Several formats sit behind Polycam Pro and the free tier has shifted over the years; GLB has historically been the dependable free mesh export. The pipeline reads its GLB, glTF, OBJ, PLY, STL, USDZ, LAS and XYZ exports. FBX is not read; pick any other format. Suggested lane: GLB or PLY.

### Scaniverse

Free, with every export format included: FBX, OBJ, GLB, USDZ, STL and PLY meshes, plus LAS point clouds. The pipeline reads all of those except FBX. Suggested lane: PLY for the mesh, plus LAS if you also want the denser cloud.

### 3d Scanner App (Laan Labs)

Free and generous: OBJ, GLB, USDZ, DAE, STL and PLY meshes, point clouds (PLY, XYZ, LAS), and a "Share All Data" raw dump with per-frame JSON, depth and confidence files. The pipeline reads the mesh and point cloud exports. The "All Data" raw dump is not parsed; its layout differs from both raw layouts this pipeline understands, and converting it to the generic package below is a sensible later addition. Suggested lane: PLY or OBJ.

### Stray Scanner

Free research-grade recorder from Stray Robots. It does no meshing on the phone at all; it writes raw sensor streams to the Files app under `Stray Scanner/<recording id>/`:

- `camera_matrix.csv`: a 3x3 intrinsic matrix, stated at RGB resolution
- `odometry.csv`: header `timestamp, frame, x, y, z, qx, qy, qz, qw`, camera-to-world pose per frame
- `depth/`: 256x192 16-bit PNGs, depth in millimetres
- `confidence/` (optional): 256x192 PNGs with ARKit confidence values 0 to 2
- `rgb.mp4` (optional): the colour video, typically 1920x1440
- sometimes `imu.csv` and other files, which are ignored

Copy the whole recording folder (or a zip of it) into `raw/` and the pipeline TSDF-fuses it directly into a coloured mesh and point cloud. Details of how it is read:

- Intrinsics are scaled from the RGB resolution down to the depth maps. The RGB size is taken from `rgb.mp4` metadata when readable, otherwise 1920x1440 is assumed and logged.
- Colour needs `imageio-ffmpeg` (`pip install imageio-ffmpeg`). Without it the fusion still runs, textured in neutral grey.
- Depth pixels with confidence below `ingest.min_confidence` (default 1, on the 0 to 2 scale) are dropped when a confidence folder is present.
- ARKit's world frame is gravity-aligned Y up; the pipeline rotates it so up becomes +Z.
- `ingest.frame_stride` (default 3) integrates every Nth frame.

This is the closest lane to the future custom capture app in `ios/`: full control, no app-side meshing decisions.

### Record3D

Records RGBD video and exports `.r3d` project files, animated USDZ, and depth video formats; the higher-fidelity streams sit behind a small one-off purchase. The `.r3d` container is not parsed yet. If a Record3D lane matters, either export a format the quick reference table covers, or convert its output into the generic raw package below; the data it captures maps onto that spec cleanly.

## Generic raw package spec

This is the pipeline's own documented layout for raw depth captures, and the target format for converting any app this page does not cover. The parser is `src/aura_scan/io/raw_rgbd.py`; this section matches it word for word.

A package is a folder, or a `.zip` of one, dropped into `raw/`. The required files may sit at the top of the package or exactly one folder down (the usual result of zipping a folder). A `.zip` is extracted to a hidden temporary folder beside the zip and cleaned up after fusion.

Required contents:

- `intrinsics.json`: a JSON object `{"width": ..., "height": ..., "fx": ..., "fy": ..., "cx": ..., "cy": ...}`. Pinhole intrinsics in pixels, stated at the resolution given by `width` and `height`. They should match the depth PNGs; when they do not, the intrinsics are scaled to the depth resolution and a warning is logged.
- `poses.csv`: one row per frame, camera-to-world, in one of two header forms:
  - `frame,m00,m01,m02,m03,m10,m11,m12,m13,m20,m21,m22,m23`: the top three rows of the 4x4 camera-to-world matrix, row-major; the bottom row is assumed to be `0 0 0 1`.
  - `frame,x,y,z,qx,qy,qz,qw`: position in metres plus a unit quaternion with the scalar last (the scipy convention).
  - If a header somehow carries both sets of columns, the matrix form wins.
- `depth/`: one 16-bit single-channel PNG per frame, values in millimetres, 0 meaning no data. Filenames are the frame number plus `.png`, with any consistent zero padding (`00042.png` and `000042.png` both work); they are matched to `poses.csv` by number.

Optional contents:

- `rgb/`: one colour image per frame, `.jpg`, `.jpeg` or `.png`, matched by frame number, any resolution (resized to the depth size). Frames without a colour image are integrated in neutral grey.

Conventions the writer of a package must honour:

- World frame: right-handed, Z up, metres. If your source is ARKit (Y up), rotate before writing, or use the Stray Scanner layout, which the pipeline rotates for you.
- Camera frame: OpenCV pinhole convention, +X right, +Y down, +Z forward.
- Poses are camera-to-world; the pipeline inverts them for TSDF integration.

Robustness rules: rows in `poses.csv` without a matching depth PNG are ignored, depth PNGs without a pose row are ignored, and unreadable individual frames are skipped with a warning. Frames are sorted by number and `ingest.frame_stride` keeps every Nth. If nothing usable remains, the run stops with an actionable error.

Config keys that shape fusion, all in `configs/default.yaml`: `mesh.tsdf.voxel_size`, `mesh.tsdf.sdf_trunc`, `mesh.tsdf.depth_scale` (default 1000.0, meaning millimetre PNGs), `mesh.tsdf.depth_trunc` (metres, deeper readings are discarded) and `ingest.frame_stride`.

## How ingest decides what a file is

For each entry at the top level of `raw/` (never recursive):

1. Hidden files (names starting with a dot), `Thumbs.db`, `desktop.ini`, and the companion extensions `.mtl`, `.png`, `.jpg`, `.jpeg`, `.bin` are skipped without comment. Top-level images in `raw/` are assumed to be textures riding along with an OBJ.
2. Folders and `.zip` files are checked for the raw package markers: `odometry.csv` plus `camera_matrix.csv` plus a `depth/` folder means Stray Scanner; `intrinsics.json` plus `poses.csv` plus a `depth/` folder means the generic spec. The markers may sit at the top or one folder down. Matches are fused as raw packages; everything else is skipped with a log line.
3. `.ply` is read as a mesh when it has faces, otherwise as a point cloud.
4. `.obj`, `.glb`, `.gltf`, `.stl` and `.dae` are read as meshes via trimesh; multi-part scenes are concatenated into one mesh. Vertex colours are kept when the file has them; textures are not baked. A mesh file with vertices but no faces degrades to a point cloud of its vertices.
5. `.pcd` and `.xyz` are read as point clouds.
6. `.las` and `.laz` are read as point clouds via laspy, keeping colour when present. Coordinates are taken as metres, as written.
7. `.usdz` is read as a mesh when `usd-core` is installed, honouring the file's declared `metersPerUnit` and up axis. Without `usd-core` the run stops with an error asking for an OBJ or PLY export instead.
8. Anything else is skipped with a log line naming the extension.

When the pipeline needs a point cloud from a mesh input, it samples `ingest.mesh_sample_points` (default 200 000) points uniformly across the surface, keeping colour.

## Known gaps, stated plainly

- **Axis conventions.** OBJ, GLB and glTF exports from ARKit apps are usually Y up (gravity along +Y) and the pipeline does not rotate them; plans, footprints and heights assume Z up, so if a plan view comes out sideways, this is why. USDZ files declare their up axis and are rotated when they say Y. Stray Scanner packages are always rotated from ARKit's Y up. A per-scan up-axis override in the manifest is a sensible Phase 2 addition.
- **Untested against live exports.** The layouts above match each app's documentation and community-documented behaviour as of mid 2026. None of the app-specific claims have been verified against a fresh export on this pipeline yet.
- **`.laz` needs a backend.** laspy reads compressed LAZ only with `lazrs` (or `laszip`) installed. Uncompressed `.las` works out of the box.
- **Zipped mesh exports are not unpacked.** Only raw depth packages are read from zips. If an app shares a zip of OBJ plus MTL plus textures, extract it into `raw/` first.
- **Record3D `.r3d` and the 3d Scanner App "All Data" dump are not parsed.** Both hold the right data; a converter to the generic package spec is the intended path when either lane is wanted.

For the field side (marker sheets, tape measurements, picking control points), see the capture guide in `docs/capture-guide.md`.
