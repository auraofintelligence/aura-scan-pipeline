"""Ingest phone exports: mesh files, point clouds and raw depth packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger, trimesh_to_o3d
from .raw_rgbd import detect_raw_package, fuse_raw_package

log = get_logger(__name__)

MESH_EXTENSIONS = {".obj", ".glb", ".gltf", ".stl", ".dae"}
POINTCLOUD_EXTENSIONS = {".pcd", ".xyz"}
LAS_EXTENSIONS = {".las", ".laz"}
KNOWN_EXTENSIONS = MESH_EXTENSIONS | POINTCLOUD_EXTENSIONS | LAS_EXTENSIONS | {".ply", ".usdz"}

# Companion files that ride along with mesh exports; never inputs in their own right.
_SKIP_EXTENSIONS = {".mtl", ".png", ".jpg", ".jpeg", ".bin"}
_SKIP_NAMES = {"thumbs.db", "desktop.ini"}


@dataclass
class IngestItem:
    path: Path
    kind: str                      # "mesh" | "pointcloud" | "raw_rgbd"
    mesh: o3d.geometry.TriangleMesh | None
    pointcloud: o3d.geometry.PointCloud | None
    notes: str = ""


def discover_inputs(raw_dir: Path) -> list[Path]:
    """List the loadable inputs in a scan's raw/ folder, skipping companions."""
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise PipelineError(f"Raw folder {raw_dir} does not exist; create the scan project first.")
    found: list[Path] = []
    for entry in sorted(raw_dir.iterdir(), key=lambda p: p.name.lower()):
        name = entry.name
        if name.startswith(".") or name.lower() in _SKIP_NAMES:
            continue
        if entry.is_dir():
            if detect_raw_package(entry):
                found.append(entry)
            else:
                log.warning("Skipping folder %s: not a recognised raw depth package", name)
            continue
        suffix = entry.suffix.lower()
        if suffix in _SKIP_EXTENSIONS:
            continue
        if suffix == ".zip":
            if detect_raw_package(entry):
                found.append(entry)
            else:
                log.warning(
                    "Skipping %s: not a raw depth package zip. If it holds a mesh "
                    "export, extract it into raw/ first.", name)
            continue
        if suffix in KNOWN_EXTENSIONS:
            found.append(entry)
        else:
            log.warning("Skipping %s: unsupported extension %s", name, suffix or "(none)")
    return found


def load_input(path: Path, cfg: dict) -> IngestItem:
    """Load one input from raw/ into Open3D geometry."""
    path = Path(path)
    if not path.exists():
        raise PipelineError(f"Input {path} does not exist")
    if path.is_dir() or path.suffix.lower() == ".zip":
        if detect_raw_package(path) is None:
            raise PipelineError(
                f"{path.name} is not a recognised raw depth package; see docs/formats.md "
                "for the Stray Scanner and generic layouts."
            )
        result = fuse_raw_package(path, cfg)
        return IngestItem(path=path, kind="raw_rgbd", mesh=result.mesh,
                          pointcloud=result.pointcloud, notes=result.notes)
    suffix = path.suffix.lower()
    if suffix == ".ply":
        return _load_ply(path)
    if suffix in MESH_EXTENSIONS:
        return _load_trimesh_file(path)
    if suffix in POINTCLOUD_EXTENSIONS:
        return _load_o3d_pointcloud(path)
    if suffix in LAS_EXTENSIONS:
        return _load_las(path)
    if suffix == ".usdz":
        return _load_usdz(path)
    raise PipelineError(
        f"Unsupported input {path.name}. Supported: "
        + ", ".join(sorted(KNOWN_EXTENSIONS))
        + ", plus raw depth package folders and zips."
    )


def to_pointcloud(item: IngestItem, cfg: dict) -> o3d.geometry.PointCloud:
    """Return the item's cloud, sampling the mesh surface when there is no cloud."""
    if item.pointcloud is not None and len(item.pointcloud.points) > 0:
        return item.pointcloud
    if item.mesh is None or len(item.mesh.vertices) == 0:
        raise PipelineError(f"{item.path.name} produced no geometry to build a point cloud from")
    if len(item.mesh.triangles) == 0:
        return o3d.geometry.PointCloud(item.mesh.vertices)
    count = max(1, int(cfg_get(cfg, "ingest.mesh_sample_points", 200_000)))
    pc = item.mesh.sample_points_uniformly(number_of_points=count)
    log.info("Sampled %d points from %s", len(pc.points), item.path.name)
    return pc


def _load_ply(path: Path) -> IngestItem:
    # A PLY is a mesh when it carries faces, otherwise a point cloud.
    mesh = o3d.io.read_triangle_mesh(str(path))
    if len(mesh.triangles) > 0:
        mesh.compute_vertex_normals()
        return IngestItem(path=path, kind="mesh", mesh=mesh, pointcloud=None,
                          notes=f"PLY mesh, {len(mesh.triangles)} triangles")
    pc = o3d.io.read_point_cloud(str(path))
    if len(pc.points) == 0:
        raise PipelineError(
            f"{path.name} contains no readable geometry; re-export it from the capture app."
        )
    return IngestItem(path=path, kind="pointcloud", mesh=None, pointcloud=pc,
                      notes=f"PLY point cloud, {len(pc.points)} points")


def _trimesh_to_o3d_with_colour(tm) -> o3d.geometry.TriangleMesh:
    mesh = trimesh_to_o3d(tm)
    # Keep vertex colours where the loader already has them; textures are not chased.
    if getattr(tm.visual, "kind", None) == "vertex":
        colours = np.asarray(tm.visual.vertex_colors, dtype=np.float64)
        if colours.ndim == 2 and colours.shape[0] == len(tm.vertices):
            mesh.vertex_colors = o3d.utility.Vector3dVector(
                np.ascontiguousarray(colours[:, :3]) / 255.0)
    return mesh


def _load_trimesh_file(path: Path) -> IngestItem:
    import trimesh

    try:
        loaded = trimesh.load(str(path), force="mesh")
    except Exception as exc:
        raise PipelineError(
            f"Could not read {path.name}: {exc}. Re-export as OBJ, GLB or PLY from "
            "the capture app (.dae also needs pycollada installed)."
        ) from exc
    notes: list[str] = []
    if isinstance(loaded, trimesh.Scene):
        parts = [g for g in loaded.dump() if isinstance(g, trimesh.Trimesh)]
        if not parts:
            raise PipelineError(f"{path.name} contains no mesh geometry")
        loaded = parts[0] if len(parts) == 1 else trimesh.util.concatenate(parts)
        notes.append(f"scene concatenated from {len(parts)} part(s)")
    if len(loaded.vertices) == 0:
        raise PipelineError(f"{path.name} contains no vertices")
    if len(loaded.faces) == 0:
        pc = o3d.geometry.PointCloud(
            o3d.utility.Vector3dVector(np.asarray(loaded.vertices, dtype=np.float64)))
        notes.append("mesh file had vertices but no faces; treated as a point cloud")
        return IngestItem(path=path, kind="pointcloud", mesh=None, pointcloud=pc,
                          notes="; ".join(notes))
    if path.suffix.lower() in {".glb", ".gltf"}:
        # glTF is Y-up by convention; the pipeline assumes Z up and does not rotate.
        log.info("%s: glTF files are usually Y-up; plans and heights assume Z up "
                 "(see docs/formats.md)", path.name)
        notes.append("glTF source, usually Y-up; coordinates kept as authored")
    mesh = _trimesh_to_o3d_with_colour(loaded)
    notes.append(f"{len(loaded.faces)} triangles")
    return IngestItem(path=path, kind="mesh", mesh=mesh, pointcloud=None,
                      notes="; ".join(notes))


def _load_o3d_pointcloud(path: Path) -> IngestItem:
    pc = o3d.io.read_point_cloud(str(path))
    if len(pc.points) == 0:
        raise PipelineError(
            f"{path.name} contains no readable points; re-export it from the capture app."
        )
    return IngestItem(path=path, kind="pointcloud", mesh=None, pointcloud=pc,
                      notes=f"{len(pc.points)} points")


def _load_las(path: Path) -> IngestItem:
    try:
        import laspy
    except ImportError as exc:
        raise PipelineError(
            f"{path.name} needs laspy (pip install laspy); or export PLY from the "
            "capture app instead."
        ) from exc
    try:
        las = laspy.read(str(path))
    except Exception as exc:
        raise PipelineError(
            f"Could not read {path.name}: {exc}. Compressed .laz needs a backend "
            "(pip install lazrs); otherwise export uncompressed .las or PLY."
        ) from exc
    points = np.column_stack(
        [np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)]).astype(np.float64)
    if len(points) == 0:
        raise PipelineError(f"{path.name} contains no points")
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    notes = [f"{len(points)} points"]
    dims = set(las.point_format.dimension_names)
    if {"red", "green", "blue"} <= dims:
        colours = np.column_stack(
            [np.asarray(las.red), np.asarray(las.green), np.asarray(las.blue)]
        ).astype(np.float64)
        # LAS colour is nominally 16 bit but some writers store 8-bit values.
        peak = float(colours.max()) if colours.size else 0.0
        colours /= 65535.0 if peak > 255.0 else 255.0
        pc.colors = o3d.utility.Vector3dVector(np.clip(colours, 0.0, 1.0))
        notes.append("colour kept")
    return IngestItem(path=path, kind="pointcloud", mesh=None, pointcloud=pc,
                      notes="; ".join(notes))


# Maps a declared Y-up frame onto the pipeline's Z-up frame (row-vector points use the transpose).
_Y_UP_TO_Z_UP = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0],
    [0.0, 1.0, 0.0],
])


def _load_usdz(path: Path) -> IngestItem:
    try:
        from pxr import Usd, UsdGeom
    except ImportError as exc:
        raise PipelineError(
            f"{path.name} is a USDZ file and usd-core is not installed. Either "
            "pip install usd-core, or export OBJ or PLY from the capture app instead."
        ) from exc

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise PipelineError(
            f"Could not open {path.name} as a USD stage; export OBJ or PLY from the "
            "capture app instead."
        )
    metres_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    up_axis = str(UsdGeom.GetStageUpAxis(stage)).upper()

    all_vertices: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    offset = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        geom = UsdGeom.Mesh(prim)
        pts = geom.GetPointsAttr().Get()
        counts = geom.GetFaceVertexCountsAttr().Get()
        indices = geom.GetFaceVertexIndicesAttr().Get()
        if pts is None or counts is None or indices is None:
            continue
        if len(pts) == 0 or len(counts) == 0 or len(indices) == 0:
            continue
        xform = np.asarray(
            UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()),
            dtype=np.float64,
        )
        vertices = np.asarray(pts, dtype=np.float64)
        # USD matrices use the row-vector convention: transformed = p @ M.
        vertices = vertices @ xform[:3, :3] + xform[3, :3]
        indices = np.asarray(indices, dtype=np.int64)
        faces: list[tuple[int, int, int]] = []
        pos = 0
        for count in counts:
            count = int(count)
            for k in range(1, count - 1):  # fan triangulation of each polygon
                faces.append((int(indices[pos]), int(indices[pos + k]), int(indices[pos + k + 1])))
            pos += count
        if faces:
            all_vertices.append(vertices)
            all_faces.append(np.asarray(faces, dtype=np.int64) + offset)
            offset += len(vertices)
    if not all_vertices:
        raise PipelineError(
            f"{path.name} holds no mesh geometry the USD reader understands; "
            "export OBJ or PLY from the capture app instead."
        )
    vertices = np.vstack(all_vertices) * metres_per_unit
    notes = [f"USDZ, {len(all_vertices)} mesh prim(s), metersPerUnit {metres_per_unit:g}"]
    if up_axis == "Y":
        vertices = vertices @ _Y_UP_TO_Z_UP.T
        notes.append("rotated declared Y-up stage to Z up")
    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(vertices),
        o3d.utility.Vector3iVector(np.vstack(all_faces).astype(np.int32)),
    )
    mesh.compute_vertex_normals()
    return IngestItem(path=path, kind="mesh", mesh=mesh, pointcloud=None,
                      notes="; ".join(notes))
