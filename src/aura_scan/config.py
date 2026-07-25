"""Layered YAML configuration: default, then mode, then per-scan overrides."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .util import PipelineError

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"


def deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_yaml(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise PipelineError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise PipelineError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


def load_config(mode: str = "general", overrides: dict | None = None,
                configs_dir: Path | None = None) -> dict:
    configs_dir = Path(configs_dir) if configs_dir else CONFIGS_DIR
    default_path = configs_dir / "default.yaml"
    if not default_path.exists():
        raise PipelineError(f"Missing {default_path}")
    cfg = _load_yaml(default_path)
    mode_path = configs_dir / f"{mode}.yaml"
    if mode_path.exists():
        cfg = deep_merge(cfg, _load_yaml(mode_path))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    cfg["_mode"] = mode
    return cfg
