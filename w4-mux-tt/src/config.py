"""Read the simple KEY=VALUE configuration used by the benchmark."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    for key in (
        "RUN_ID", "CONGESTION_LOG_DIR", "SERVER_CONGESTION_LOG_DIR",
        "REMOTE_CONGESTION_SAMPLER",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def cfg_bool(cfg: dict[str, str], key: str, default: str = "0") -> bool:
    return cfg.get(key, default).strip().lower() in {"1", "true", "yes", "on"}
