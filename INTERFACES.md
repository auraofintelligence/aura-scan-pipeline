# Module contracts

Binding contracts between `aura_scan` modules. `pipeline.py` calls exactly these signatures. If an implementation needs to deviate, change this file and `pipeline.py` in the same commit.

Conventions for every module:

- Internal units are metres, Z up, `float64` NumPy arrays.
- Geometry passes between modules as `open3d.geometry.PointCloud` / `open3d.geometry.TriangleMesh`. Convert to and from `trimesh.Trimesh` internally where trimesh is stronger (sections, GLB export, oriented bounds) via `util.o3d_mesh_to_trimesh` / `util.trimesh_to_o3d`.
- `cfg` is the plain nested dict from `config.load_config`. Read keys with `util.cfg_get(cfg, "a.b.c", default)` and fall back to sane defaults so a missing key never crashes.
- Raise `util.PipelineError("actionable message")` for user-fixable failures (empty input, unreadable file, degenerate geometry). Let genuine bugs raise normally.
- Log through `util.get_logger(__name__)`, not print.
- Stats dicts are JSON-serialisable (no NumPy scalars; cast with `float()` / `int()`).
- Optional dependencies (`pymeshlab`, `laspy`, `imageio-ffmpeg`, `usd-core`) are imported inside functions with graceful fallback; their absence degrades a feature, never crashes the run.

## util.py (exists)

`PipelineError`, `get_logger(name)`, `cfg_get(cfg, dotted, default=None)`, `ensure_dir(path) -> Path`, `now_iso() -> str`, `o3d_mesh_to_trimesh(m) -> trimesh.Trimesh`, `trimesh_to_o3d(tm) -> o3d TriangleMesh`, `np_to_list(x)` (recursive JSON-safe cast).

## config.py (exists)

`load_config(mode="general", overrides=None, configs_dir=None) -> dict` and `deep_merge(base, override) -> dict`. Merge order: `default.yaml`, then `<mode>.yaml` when present, then `overrides`.

## project.py (exists)

`ScanProject.init(parent, name, mode)` creates `scans/<name>/{manifest.yaml, raw/, work/, exports/}`. `ScanProject(root)` loads. Attributes: `root, name, mode, manifest (dict), raw_dir, work_dir, exports_dir`, `save_manifest()`.

## io/ingest.py

```python
@dataclass
class IngestItem:
    path: Path
    kind: str                      # "mesh" | "pointcloud" | "raw_rgbd"
    mesh: o3d.geometry.TriangleMesh | None
    pointcloud: o3d.geometry.PointCloud | None
    notes: str = ""

def discover_inputs(raw_dir: Path) -> list[Path]
    # Files with known extensions plus directories/zips that detect_raw_package recognises.
    # Skip hidden files, textures (.png/.jpg alongside an .obj), .mtl.

def load_input(path: Path, cfg: dict) -> IngestItem
    # .obj/.glb/.gltf/.stl/.dae -> mesh (via trimesh, force="mesh", concatenate scenes)
    # .ply -> mesh if it has faces else pointcloud; .pcd/.xyz -> pointcloud
    # .las/.laz -> pointcloud (laspy); .usdz -> mesh via usd-core if installed, else PipelineError with guidance
    # raw package (dir or zip) -> io.raw_rgbd.fuse_raw_package, kind "raw_rgbd", may set both mesh and pointcloud

def to_pointcloud(item: IngestItem, cfg: dict) -> o3d.geometry.PointCloud
    # pointcloud passthrough; mesh -> sample_points_uniformly (count from cfg ingest.mesh_sample_points, default 200_000),
    # keep colours where present
```

## io/raw_rgbd.py

```python
def detect_raw_package(path: Path) -> str | None   # "stray" | "generic" | None
    # stray: contains odometry.csv + camera_matrix.csv + depth/ (Stray Scanner export)
    # generic: contains intrinsics.json + poses.csv + depth/   (our documented spec, see docs/formats.md)

@dataclass
class FusedResult:
    pointcloud: o3d.geometry.PointCloud
    mesh: o3d.geometry.TriangleMesh | None
    frames_used: int
    notes: str

def fuse_raw_package(path: Path, cfg: dict) -> FusedResult
    # TSDF integration (o3d.pipelines.integration.ScalableTSDFVolume) of uint16 millimetre depth PNGs
    # with per-frame camera poses. RGB optional (stray rgb.mp4 via imageio-ffmpeg when available,
    # else integrate geometry only with a neutral grey colour image). Extract both mesh and point cloud.
    # cfg keys: mesh.tsdf.voxel_size, .sdf_trunc, .depth_scale (default 1000.0), .depth_trunc, ingest.frame_stride.
```

## ops/clean.py

```python
def clean_pointcloud(pc, cfg) -> tuple[o3d.geometry.PointCloud, dict]
    # optional voxel downsample (clean.voxel_size, 0 disables), statistical outlier removal,
    # optional radius outlier removal, optional keep-largest-cluster (DBSCAN).
    # stats: points_in, points_out, removed_outliers, ...

def clean_mesh(mesh, cfg) -> tuple[o3d.geometry.TriangleMesh, dict]
    # remove duplicated/degenerate/unreferenced, optional keep-largest-component (clean.keep_largest_cluster)
```

## ops/register.py

```python
@dataclass
class RegResult:
    transform: np.ndarray  # 4x4
    fitness: float
    inlier_rmse: float

def register_icp(source_pc, target_pc, cfg, init=None) -> RegResult
    # point-to-plane ICP (normals estimated as needed), cfg register.icp.*

def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = False) -> tuple[np.ndarray, float, np.ndarray]
    # (N,3),(N,3) -> (T 4x4, scale, per-point residuals after transform)

def merge_pointclouds(pcs: list, cfg) -> tuple[o3d.geometry.PointCloud, list[RegResult]]
    # register each subsequent cloud to the first, concatenate, voxel dedupe at clean.voxel_size
```

## ops/scale.py

```python
@dataclass
class DistanceConstraint:
    a: np.ndarray   # (3,)
    b: np.ndarray   # (3,)
    distance_m: float
    note: str = ""

@dataclass
class ScaleResult:
    factor: float
    rmse_before_m: float
    rmse_after_m: float
    max_error_after_m: float
    constraints: list[dict]   # per-constraint {note, distance_m, measured_m, error_before_m, error_after_m}

def solve_scale(constraints: list[DistanceConstraint]) -> ScaleResult
    # least-squares uniform factor = sum(real*measured)/sum(measured^2)

def apply_scale(geometry, factor: float) -> None   # in place, about the origin; pc or mesh

def verification_block(result: ScaleResult | None, applied: bool) -> dict
    # the metadata "scale" block; method "arkit" when result is None
```

## ops/meshing.py

```python
def estimate_normals(pc, cfg) -> None            # in place, KNN from mesh.normals.knn, orient consistently
def pointcloud_to_mesh(pc, cfg) -> o3d.geometry.TriangleMesh
    # Poisson (mesh.poisson.depth) then crop by density quantile (mesh.poisson.density_quantile);
    # on failure or empty result fall back to ball pivoting; PipelineError if both fail.
def repair_mesh(mesh, cfg) -> tuple[o3d.geometry.TriangleMesh, dict]
    # basic o3d cleanup always; pymeshlab close-holes/repair when importable (stats say which path ran)
```

## ops/simplify.py

```python
@dataclass
class LodSet:
    lod0_box: o3d.geometry.TriangleMesh     # oriented bounding box as mesh
    footprint_2d: np.ndarray                # (N,2) closed outline, metres, XY plane
    lod1: o3d.geometry.TriangleMesh
    lod2: o3d.geometry.TriangleMesh
    stats: dict

def make_lods(mesh, cfg) -> LodSet
    # lod2: input capped at lods.lod2_max_triangles; lod1: quadric decimation to lods.lod1_max_triangles.
    # footprint: project vertices to XY, shapely concave hull (or convex fallback), simplify at lods.footprint_simplify_m.
```

## ops/section.py

```python
@dataclass
class SectionSpec:
    name: str
    origin: np.ndarray   # (3,)
    normal: np.ndarray   # (3,) unit

@dataclass
class SectionResult:
    spec: SectionSpec
    polylines: list[np.ndarray]   # each (N,2), metres, in the section plane's 2D frame
    extent_2d: tuple[float, float]

def auto_section_specs(mesh, cfg) -> list[SectionSpec]
    # "plan" horizontal cut at min_z + sections.plan_height, plus "long-section"/"cross-section"
    # vertical planes through the oriented-bounds centre along the two principal horizontal axes

def cut_sections(mesh, specs: list[SectionSpec], cfg) -> list[SectionResult]
    # trimesh mesh.section(...) -> to_2D; empty intersections are skipped with a log line, never an error
```

## ops/plane.py

```python
@dataclass
class PlaneReport:
    origin: np.ndarray
    normal: np.ndarray
    rmse_m: float
    inlier_ratio: float
    boundary_2d: np.ndarray        # (N,2) usable-area outline in the plane frame
    usable_area_m2: float
    suitable: bool                 # rmse <= projection.max_rmse and inlier_ratio >= projection.min_inlier_ratio
    plane_mesh: o3d.geometry.TriangleMesh   # flat simplified surface for projection mapping

def analyse_projection_surface(pc, cfg) -> PlaneReport   # RANSAC segment_plane, cfg projection.*
```

## ops/asset.py

```python
@dataclass
class AssetReport:
    dims_m: dict          # {"length": float, "width": float, "height": float}  length >= width, height = vertical extent
    obb_centre: np.ndarray
    obb_axes: np.ndarray  # 3x3 rows
    footprint_2d: np.ndarray
    key_heights_m: list[float]    # peaks of the vertex-height histogram (seat tops, table tops)
    stats: dict

def analyse_asset(mesh, cfg) -> AssetReport
    # trimesh.bounds.oriented_bounds for tight dims; keep height as world-Z extent
```

## export/dxf.py

```python
def export_dxf(path: Path, *, footprint_2d, sections: list[SectionResult],
               asset_report=None, plane_report=None, meta_lines: list[str], cfg) -> Path
    # ezdxf R2018. $INSUNITS from dxf.units ("mm" -> 4, "m" -> 6); geometry scaled accordingly.
    # Plan at origin (footprint on ASSET_OUTLINE or EXISTING, plane boundary on PROJECTION_SURFACE),
    # sections laid out in a row to the right on SECTIONS, overall linear dimensions (add_linear_dim + render)
    # on DIMENSIONS for plan extents and each section's extents, meta_lines as MTEXT on TEXT.
    # Layer names from dxf.layers.*.
```

## export/mesh_export.py

```python
def export_mesh(mesh, path: Path) -> Path     # .obj/.ply/.stl via o3d; .glb via trimesh with Z-up to Y-up conversion
def export_lods(lods: LodSet, out_dir: Path, basename: str) -> dict[str, str]
    # writes <basename>_lod1.glb, _lod1.obj, _lod2.obj, _lod2.ply; returns metadata geometry_files fragment
```

## export/pointcloud_export.py

```python
def export_pointcloud(pc, out_dir: Path, basename: str, cfg) -> dict[str, str]
    # always .ply; .las via laspy when importable; formats list from export.pointcloud_formats
```

## export/metadata.py

```python
def build_metadata(project, cfg, *, scale_block, asset_report=None, plane_report=None,
                   geometry_files: dict, stage_stats: dict) -> dict     # schema aura-scan-metadata/2.0, see DESIGN.md
def write_metadata(meta: dict, exports_dir: Path) -> Path               # metadata.json, validates required keys first
```

## export/web.py

```python
def render_thumbnail(mesh, path: Path, cfg) -> Path | None   # matplotlib 3D render, honest placeholder quality
def export_web(project, meta: dict, lods: LodSet, exports_dir: Path, cfg) -> dict[str, str]
    # places-entry.json: {"placeId": links.places_link_id, "model": {...}, "sensoriumEvidence": {...}}
    # viewer-snippet.html: <model-viewer> block referencing the LOD1 GLB with a note that
    # model-viewer.min.js must be vendored or CDN-loaded by the host page
```

## pipeline.py (exists)

`run_scan(project, cfg=None) -> dict` orchestrates the stages in DESIGN.md order and returns the summary written to `exports/run-summary.json`.

## cli.py (exists)

`aura-scan init|pick|run|report` (argparse; `python -m aura_scan` also works).
