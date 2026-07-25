"""Mesh exports: OBJ, PLY and STL via Open3D; GLB via trimesh with Y up."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import open3d as o3d

from ..util import PipelineError, ensure_dir, get_logger, o3d_mesh_to_trimesh

if TYPE_CHECKING:
    from ..ops.simplify import LodSet

log = get_logger(__name__)

_O3D_FORMATS = {".obj", ".ply", ".stl"}


def export_mesh(mesh: o3d.geometry.TriangleMesh, path: Path) -> Path:
    """Write one mesh; the format follows the file extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if len(mesh.triangles) == 0:
        raise PipelineError(
            f"Refusing to export an empty mesh to {path.name}: the reconstruction "
            "produced no triangles. Check the inputs in raw/ and the mesh.* settings."
        )
    ensure_dir(path.parent)
    if suffix == ".glb":
        _export_glb(mesh, path)
    elif suffix in _O3D_FORMATS:
        mesh.compute_vertex_normals()
        mesh.compute_triangle_normals()  # STL needs face normals; harmless elsewhere
        if not o3d.io.write_triangle_mesh(str(path), mesh):
            raise PipelineError(f"Open3D could not write {path}. Is the folder writable?")
    else:
        raise PipelineError(
            f"Unsupported mesh format '{suffix}' for {path.name}; "
            "use .obj, .ply, .stl or .glb"
        )
    log.info("Wrote %s (%d triangles)", path.name, len(mesh.triangles))
    return path


def _export_glb(mesh: o3d.geometry.TriangleMesh, path: Path) -> None:
    # glTF is Y up while the internal frame is Z up, so rotate a copy
    # by -90 degrees about X before export. The source mesh is untouched:
    # o3d_mesh_to_trimesh builds a fresh Trimesh from copies of the arrays.
    import trimesh

    tm = o3d_mesh_to_trimesh(mesh)
    tm.apply_transform(
        trimesh.transformations.rotation_matrix(-np.pi / 2.0, (1.0, 0.0, 0.0))
    )
    try:
        tm.export(str(path))
    except OSError as exc:
        raise PipelineError(f"Could not write GLB to {path}: {exc}") from exc


def export_lods(lods: LodSet, out_dir: Path, basename: str) -> dict[str, str]:
    """Write the web and archival tiers; return the geometry_files fragment."""
    out_dir = ensure_dir(Path(out_dir))
    plan = [
        ("lod1_glb", lods.lod1, f"{basename}_lod1.glb"),
        ("lod1_obj", lods.lod1, f"{basename}_lod1.obj"),
        ("lod2_obj", lods.lod2, f"{basename}_lod2.obj"),
        ("lod2_ply", lods.lod2, f"{basename}_lod2.ply"),
    ]
    files: dict[str, str] = {}
    for key, mesh, name in plan:
        export_mesh(mesh, out_dir / name)
        files[key] = name
    return files
