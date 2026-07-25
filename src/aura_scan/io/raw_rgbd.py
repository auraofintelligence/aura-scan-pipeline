"""Raw depth package fusion: Stray Scanner exports and the generic spec.

Layouts are documented in docs/formats.md; the parser here is the source of truth.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import open3d as o3d

from ..util import PipelineError, cfg_get, get_logger

log = get_logger(__name__)

STRAY_MARKERS = ("odometry.csv", "camera_matrix.csv")
GENERIC_MARKERS = ("intrinsics.json", "poses.csv")

# Stray Scanner intrinsics are stated at RGB resolution; this is the usual size
# when rgb.mp4 metadata cannot tell us otherwise.
_DEFAULT_STRAY_RGB_SIZE = (1920, 1440)

# ARKit's world frame is gravity-aligned Y up; the pipeline is Z up.
_Y_UP_TO_Z_UP = np.array([
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, -1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
])


@dataclass
class FusedResult:
    pointcloud: o3d.geometry.PointCloud
    mesh: o3d.geometry.TriangleMesh | None
    frames_used: int
    notes: str


@dataclass
class _Frame:
    index: int
    pose_c2w: np.ndarray               # 4x4 camera-to-world
    depth_path: Path
    rgb_path: Path | None = None
    confidence_path: Path | None = None


def _dir_package_kind(root: Path) -> str | None:
    if all((root / m).is_file() for m in STRAY_MARKERS) and (root / "depth").is_dir():
        return "stray"
    if all((root / m).is_file() for m in GENERIC_MARKERS) and (root / "depth").is_dir():
        return "generic"
    return None


def _find_package_root(path: Path) -> tuple[Path, str] | None:
    kind = _dir_package_kind(path)
    if kind:
        return path, kind
    # A zip usually extracts to a single wrapping folder; look one level down.
    for child in sorted(p for p in path.iterdir() if p.is_dir()):
        kind = _dir_package_kind(child)
        if kind:
            return child, kind
    return None


def _zip_package_kind(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    prefixes = {""} | {n.split("/", 1)[0] + "/" for n in names if "/" in n}
    for prefix in sorted(prefixes):
        if not any(n.startswith(prefix + "depth/") for n in names):
            continue
        if all(prefix + m in names for m in STRAY_MARKERS):
            return "stray"
        if all(prefix + m in names for m in GENERIC_MARKERS):
            return "generic"
    return None


def detect_raw_package(path: Path) -> str | None:
    """Return "stray" or "generic" when path is a raw depth package, else None."""
    path = Path(path)
    if path.is_dir():
        found = _find_package_root(path)
        return found[1] if found else None
    if path.is_file() and path.suffix.lower() == ".zip":
        return _zip_package_kind(path)
    return None


def fuse_raw_package(path: Path, cfg: dict) -> FusedResult:
    """TSDF-fuse a raw depth package (folder or zip) into a cloud and mesh."""
    path = Path(path)
    temp_dir: Path | None = None
    try:
        if path.is_file() and path.suffix.lower() == ".zip":
            # Hidden prefix so a crash leftover is never re-discovered as an input.
            temp_dir = Path(tempfile.mkdtemp(prefix=f".{path.stem}-", dir=path.parent))
            log.info("Extracting %s beside it for fusion", path.name)
            try:
                with zipfile.ZipFile(path) as zf:
                    zf.extractall(temp_dir)
            except (zipfile.BadZipFile, OSError) as exc:
                raise PipelineError(f"Could not extract {path}: {exc}") from exc
            package_dir = temp_dir
        elif path.is_dir():
            package_dir = path
        else:
            raise PipelineError(f"{path} is not a folder or .zip raw depth package")
        found = _find_package_root(package_dir)
        if found is None:
            raise PipelineError(
                f"{path.name} is not a recognised raw depth package. Expected a Stray "
                "Scanner export (odometry.csv + camera_matrix.csv + depth/) or the "
                "generic layout (intrinsics.json + poses.csv + depth/); see docs/formats.md."
            )
        root, kind = found
        return _fuse(root, kind, cfg, source_name=path.name)
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _pose_from_quaternion(x: float, y: float, z: float,
                          qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    pose = np.eye(4)
    pose[:3, :3] = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
    pose[:3, 3] = (x, y, z)
    return pose


def _indexed_files(folder: Path, suffixes: tuple[str, ...]) -> dict[int, Path]:
    """Map frame number to file for folders of NNNNN.<ext> files, any zero padding."""
    files: dict[int, Path] = {}
    if not folder.is_dir():
        return files
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in suffixes:
            continue
        try:
            files[int(p.stem)] = p
        except ValueError:
            continue
    return files


def _stripped_dict_reader(fh) -> csv.DictReader:
    reader = csv.DictReader(fh)
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]
    return reader


def _load_stray_frames(root: Path) -> tuple[dict[str, float], list[_Frame], Path | None]:
    matrix_path = root / "camera_matrix.csv"
    camera_matrix = np.loadtxt(matrix_path, delimiter=",", dtype=float)
    if camera_matrix.shape != (3, 3):
        raise PipelineError(f"{matrix_path} is not a 3x3 camera matrix")
    depth_files = _indexed_files(root / "depth", (".png",))
    if not depth_files:
        raise PipelineError(f"No depth PNGs found in {root / 'depth'}")
    confidence_files = _indexed_files(root / "confidence", (".png",))
    odometry_path = root / "odometry.csv"
    frames: list[_Frame] = []
    with open(odometry_path, newline="", encoding="utf-8") as fh:
        for row in _stripped_dict_reader(fh):
            try:
                idx = int(float(row["frame"]))
            except (KeyError, TypeError, ValueError):
                continue
            if idx not in depth_files:
                continue
            try:
                pose = _pose_from_quaternion(
                    float(row["x"]), float(row["y"]), float(row["z"]),
                    float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise PipelineError(f"Malformed row in {odometry_path}: {row}") from exc
            frames.append(_Frame(
                index=idx,
                pose_c2w=_Y_UP_TO_Z_UP @ pose,
                depth_path=depth_files[idx],
                confidence_path=confidence_files.get(idx),
            ))
    if not frames:
        raise PipelineError(
            f"{odometry_path} has no rows matching the PNGs in depth/; "
            "check the export copied across completely."
        )
    intrinsics = {
        "fx": float(camera_matrix[0, 0]), "fy": float(camera_matrix[1, 1]),
        "cx": float(camera_matrix[0, 2]), "cy": float(camera_matrix[1, 2]),
    }
    video = root / "rgb.mp4"
    return intrinsics, frames, video if video.is_file() else None


def _load_generic_frames(root: Path) -> tuple[dict[str, float], list[_Frame], Path | None]:
    intr_path = root / "intrinsics.json"
    try:
        with open(intr_path, "r", encoding="utf-8") as fh:
            intr = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Could not read {intr_path}: {exc}") from exc
    missing = [k for k in ("width", "height", "fx", "fy", "cx", "cy") if k not in intr]
    if missing:
        raise PipelineError(f"{intr_path} is missing keys: {', '.join(missing)}")
    depth_files = _indexed_files(root / "depth", (".png",))
    if not depth_files:
        raise PipelineError(f"No depth PNGs found in {root / 'depth'}")
    rgb_files = _indexed_files(root / "rgb", (".jpg", ".jpeg", ".png"))
    poses_path = root / "poses.csv"
    matrix_cols = [f"m{r}{c}" for r in range(3) for c in range(4)]
    frames: list[_Frame] = []
    with open(poses_path, newline="", encoding="utf-8") as fh:
        reader = _stripped_dict_reader(fh)
        fields = set(reader.fieldnames or [])
        matrix_form = set(matrix_cols) <= fields
        quaternion_form = {"x", "y", "z", "qx", "qy", "qz", "qw"} <= fields
        if not (matrix_form or quaternion_form):
            raise PipelineError(
                f"{poses_path} header must be frame,m00..m23 or "
                "frame,x,y,z,qx,qy,qz,qw (see docs/formats.md)"
            )
        for row in reader:
            try:
                idx = int(float(row["frame"]))
            except (KeyError, TypeError, ValueError):
                continue
            if idx not in depth_files:
                continue
            try:
                if matrix_form:
                    pose = np.eye(4)
                    pose[:3, :] = np.array(
                        [float(row[c]) for c in matrix_cols], dtype=float).reshape(3, 4)
                else:
                    pose = _pose_from_quaternion(
                        float(row["x"]), float(row["y"]), float(row["z"]),
                        float(row["qx"]), float(row["qy"]), float(row["qz"]),
                        float(row["qw"]),
                    )
            except (TypeError, ValueError) as exc:
                raise PipelineError(f"Malformed row in {poses_path}: {row}") from exc
            frames.append(_Frame(index=idx, pose_c2w=pose,
                                 depth_path=depth_files[idx], rgb_path=rgb_files.get(idx)))
    if not frames:
        raise PipelineError(
            f"{poses_path} has no rows matching the PNGs in depth/; frame numbers in "
            "the csv must match the depth filenames."
        )
    return intr, frames, None


def _read_depth(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as img:
        depth = np.asarray(img)
    if depth.ndim != 2:
        raise PipelineError(f"{path} is not a single-channel depth image")
    if depth.dtype != np.uint16:
        # PIL can hand back int32 or byte-swapped arrays for 16-bit PNGs.
        depth = np.clip(depth.astype(np.int64), 0, 65535).astype(np.uint16)
    return np.ascontiguousarray(depth)


def _apply_confidence(depth: np.ndarray, confidence_path: Path, min_confidence: int) -> np.ndarray:
    from PIL import Image

    try:
        with Image.open(confidence_path) as img:
            confidence = np.asarray(img)
    except OSError as exc:
        log.warning("Could not read %s (%s); keeping unmasked depth", confidence_path.name, exc)
        return depth
    if confidence.ndim != 2 or confidence.shape != depth.shape:
        return depth
    depth = depth.copy()
    depth[confidence < min_confidence] = 0
    return depth


def _grey_image(height: int, width: int) -> np.ndarray:
    return np.full((height, width, 3), 128, dtype=np.uint8)


def _resize_colour(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    arr = np.asarray(frame)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[2] > 3:
        arr = arr[:, :, :3]
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.shape[0] != height or arr.shape[1] != width:
        arr = np.asarray(Image.fromarray(arr).resize((width, height), Image.Resampling.BILINEAR))
    return np.ascontiguousarray(arr)


def _open_video(video_path: Path):
    """Return (reader, (width, height)) for rgb.mp4, or (None, None) when unreadable."""
    try:
        import imageio

        reader = imageio.get_reader(str(video_path))
        size = None
        try:
            meta = reader.get_meta_data() or {}
            if meta.get("size"):
                size = tuple(int(v) for v in meta["size"])
        except Exception:
            size = None
        return reader, size
    except Exception as exc:
        log.warning(
            "Could not open %s (%s); install imageio-ffmpeg for colour fusion, "
            "integrating with neutral grey instead.", video_path.name, exc)
        return None, None


def _fuse(root: Path, kind: str, cfg: dict, source_name: str) -> FusedResult:
    stride = max(1, int(cfg_get(cfg, "ingest.frame_stride", 3)))
    min_confidence = int(cfg_get(cfg, "ingest.min_confidence", 1))
    voxel = float(cfg_get(cfg, "mesh.tsdf.voxel_size", 0.01))
    sdf_trunc = float(cfg_get(cfg, "mesh.tsdf.sdf_trunc", 0.04))
    depth_scale = float(cfg_get(cfg, "mesh.tsdf.depth_scale", 1000.0))
    depth_trunc = float(cfg_get(cfg, "mesh.tsdf.depth_trunc", 5.0))

    if kind == "stray":
        intr, frames, video_path = _load_stray_frames(root)
    else:
        intr, frames, video_path = _load_generic_frames(root)
    frames.sort(key=lambda f: f.index)
    selected = frames[::stride]

    video_reader = None
    video_size: tuple[int, int] | None = None
    if video_path is not None:
        video_reader, video_size = _open_video(video_path)

    first_depth = _read_depth(selected[0].depth_path)
    height, width = first_depth.shape

    if kind == "stray":
        # camera_matrix.csv is at RGB resolution; scale it to the depth maps.
        rgb_w, rgb_h = video_size if video_size else _DEFAULT_STRAY_RGB_SIZE
        if not video_size:
            log.info("Assuming %dx%d RGB resolution for intrinsic scaling", rgb_w, rgb_h)
        scale_x, scale_y = width / float(rgb_w), height / float(rgb_h)
    else:
        stated = (int(intr["width"]), int(intr["height"]))
        if stated != (width, height):
            log.warning("intrinsics.json says %dx%d but depth PNGs are %dx%d; scaling intrinsics",
                        stated[0], stated[1], width, height)
            scale_x, scale_y = width / float(stated[0]), height / float(stated[1])
        else:
            scale_x = scale_y = 1.0
    intrinsic = o3d.camera.PinholeCameraIntrinsic(
        width, height,
        float(intr["fx"]) * scale_x, float(intr["fy"]) * scale_y,
        float(intr["cx"]) * scale_x, float(intr["cy"]) * scale_y,
    )

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel,
        sdf_trunc=sdf_trunc,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )

    frames_used = 0
    colour_frames = 0
    video_iter = iter(video_reader) if video_reader is not None else None
    video_index = -1
    video_frame: np.ndarray | None = None

    for frame in selected:
        try:
            depth = _read_depth(frame.depth_path)
        except (OSError, PipelineError) as exc:
            log.warning("Skipping frame %d: %s", frame.index, exc)
            continue
        if depth.shape != (height, width):
            log.warning("Skipping frame %d: depth is %s, expected %s",
                        frame.index, depth.shape, (height, width))
            continue
        if frame.confidence_path is not None and min_confidence > 0:
            depth = _apply_confidence(depth, frame.confidence_path, min_confidence)

        colour: np.ndarray | None = None
        if frame.rgb_path is not None:
            try:
                from PIL import Image

                with Image.open(frame.rgb_path) as img:
                    colour = np.asarray(img.convert("RGB"))
            except OSError as exc:
                log.warning("Could not read %s (%s); using grey", frame.rgb_path.name, exc)
        elif video_iter is not None:
            try:
                while video_index < frame.index:
                    video_frame = next(video_iter)
                    video_index += 1
                colour = video_frame
            except StopIteration:
                video_iter = None
            except Exception as exc:
                log.warning("Video decode failed at frame %d (%s); grey from here on",
                            frame.index, exc)
                video_iter = None
        if colour is not None:
            colour = _resize_colour(colour, width, height)
            colour_frames += 1
        else:
            colour = _grey_image(height, width)

        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(colour),
            o3d.geometry.Image(depth),
            depth_scale=depth_scale,
            depth_trunc=depth_trunc,
            convert_rgb_to_intensity=False,
        )
        try:
            extrinsic = np.linalg.inv(frame.pose_c2w)   # open3d wants world-to-camera
        except np.linalg.LinAlgError as exc:
            raise PipelineError(
                f"Pose for frame {frame.index} in {source_name} is not invertible; "
                "poses must be rigid camera-to-world transforms."
            ) from exc
        volume.integrate(rgbd, intrinsic, extrinsic)
        frames_used += 1

    if video_reader is not None:
        try:
            video_reader.close()
        except Exception:
            pass

    if frames_used == 0:
        raise PipelineError(
            f"No usable frames in {source_name}; check that depth/ PNGs and the pose "
            "file agree on frame numbers."
        )

    pointcloud = volume.extract_point_cloud()
    mesh = volume.extract_triangle_mesh()
    if len(mesh.triangles) == 0:
        mesh = None
    else:
        mesh.compute_vertex_normals()
    if len(pointcloud.points) == 0:
        if mesh is None:
            raise PipelineError(
                f"TSDF fusion of {source_name} produced no geometry. Try a smaller "
                "mesh.tsdf.voxel_size, or check the poses are camera-to-world in metres "
                "and the depth PNGs are 16-bit millimetres."
            )
        sample_count = max(1, int(cfg_get(cfg, "ingest.mesh_sample_points", 200_000)))
        pointcloud = mesh.sample_points_uniformly(number_of_points=sample_count)

    notes = (f"{kind} raw package: {frames_used}/{len(frames)} frames integrated "
             f"(stride {stride}), colour on {colour_frames}/{frames_used} frame(s)")
    if kind == "stray":
        notes += ", ARKit Y-up world rotated to Z up"
    log.info("%s", notes)
    return FusedResult(pointcloud=pointcloud, mesh=mesh, frames_used=frames_used, notes=notes)
