"""Đọc và kiểm tra cấu hình thí nghiệm.

Cấu hình được kiểm tra đầy đủ **trước** khi mở connection đầu tiên. Một thí
nghiệm chạy nhiều giờ không được phép hỏng giữa chừng vì một giá trị sai kiểu
hoặc một tổ hợp giao thức/kịch bản không tồn tại.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from stream_mux.capability import KNOWN_PROTOCOLS
from .experiment import (
    DEFAULT_TRIALS_PER_CONFIGURATION,
    ExperimentMatrix,
    Scenario,
    build_matrix,
)


class ConfigurationError(ValueError):
    """Cấu hình không hợp lệ, kèm tên khoá và nguồn để sửa được ngay."""


@dataclass(frozen=True)
class Settings:
    """Truy cập có kiểu vào một tệp cấu hình KEY=VALUE."""

    values: dict[str, str]
    source: Path

    def _fail(self, key: str, reason: str):
        return ConfigurationError(f"{self.source}: {key} {reason}")

    def text(self, key: str, default: str = "") -> str:
        return self.values.get(key, default).strip()

    def required_text(self, key: str) -> str:
        value = self.text(key)
        if not value or value == "CHANGE_ME":
            raise self._fail(key, "chưa được đặt")
        return value

    def integer(self, key: str, default: int, minimum: int | None = None) -> int:
        raw = self.text(key)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise self._fail(key, f"phải là số nguyên, nhận {raw!r}") from exc
        if minimum is not None and value < minimum:
            raise self._fail(key, f"phải >= {minimum}, nhận {value}")
        return value

    def number(self, key: str, default: float, minimum: float | None = None) -> float:
        raw = self.text(key)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError as exc:
            raise self._fail(key, f"phải là số, nhận {raw!r}") from exc
        if minimum is not None and value < minimum:
            raise self._fail(key, f"phải >= {minimum}, nhận {value}")
        return value

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self.text(key).lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def csv_list(self, key: str, default=()) -> tuple[str, ...]:
        raw = self.text(key)
        if not raw:
            return tuple(default)
        return tuple(item.strip() for item in raw.split(",") if item.strip())

    def path(self, key: str, default: str) -> Path:
        return Path(self.text(key) or default)


@dataclass(frozen=True)
class ExperimentPlan:
    """Phần cấu hình chung cho mọi workload, đã được kiểm tra."""

    protocols: tuple[str, ...]
    editors: tuple[str, ...]
    scenarios: tuple[Scenario, ...]
    matrix: ExperimentMatrix
    trials: int
    seed: int
    result_dir: Path
    run_id: str
    warmup_seconds: float
    inter_trial_delay_seconds: float

    @property
    def total_trials(self) -> int:
        return len(self.matrix) * self.trials


# Đọc một cờ boolean từ dict cấu hình thô.
def cfg_bool(values: dict, key: str, default: str = "0") -> bool:
    """Dành cho các tầng vẫn nhận dict thô thay vì `Settings`."""
    return values.get(key, default).strip().lower() in {"1", "true", "yes", "on"}


# Khoá điều khiển lần chạy: môi trường ghi đè được kể cả khi config.env không
# khai báo, để smoke-test và ablation không phải sửa tệp cấu hình.
ENVIRONMENT_ONLY_KEYS = (
    "RUN_ID",
    "RESULT_DIR",
    "TRIALS_PER_COMBINATION",
    "PROTOCOLS",
    "SCENARIOS",
    "EDITORS",
    "RANDOM_SEED",
)


# Đọc tệp KEY=VALUE; biến môi trường cùng tên được ưu tiên.
def load_settings(path) -> Settings:
    source = Path(path)
    if not source.exists():
        raise ConfigurationError(f"không có tệp cấu hình: {source}")
    values: dict[str, str] = {}
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    for key in tuple(values) + ENVIRONMENT_ONLY_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings(values, source.resolve())


# Dựng kế hoạch thí nghiệm chung và loại trước mọi tổ hợp không hợp lệ.
def build_plan(
    settings: Settings,
    scenarios: dict[str, Scenario],
    *,
    default_seed: int,
    editors: dict[str, str] | None = None,
    run_id: str = "",
    supported_protocols=KNOWN_PROTOCOLS,
) -> ExperimentPlan:
    # `supported_protocols` là danh sách của workload, có thể hẹp hơn tập giao
    # thức mà transport hỗ trợ: W4 không đánh giá Mosh vì kịch bản tải nền cần
    # workload chạy song song với editor.
    protocols = settings.csv_list("PROTOCOLS", supported_protocols)
    unknown = sorted(set(protocols) - set(supported_protocols))
    if unknown:
        raise ConfigurationError(
            f"{settings.source}: PROTOCOLS chứa giá trị workload không đánh giá: "
            f"{unknown} (hợp lệ: {', '.join(supported_protocols)})"
        )
    if len(protocols) != len(set(protocols)):
        raise ConfigurationError(f"{settings.source}: PROTOCOLS có giá trị lặp")

    selected = settings.csv_list("SCENARIOS", tuple(scenarios))
    unknown = sorted(set(selected) - set(scenarios))
    if unknown:
        raise ConfigurationError(
            f"{settings.source}: SCENARIOS chứa giá trị lạ: {unknown}"
        )
    if len(selected) != len(set(selected)):
        raise ConfigurationError(f"{settings.source}: SCENARIOS có giá trị lặp")
    chosen = tuple(scenarios[name] for name in selected)

    editor_list: tuple[str, ...] = ()
    if editors is not None:
        editor_list = settings.csv_list("EDITORS", tuple(editors))
        unknown = sorted(set(editor_list) - set(editors))
        if unknown:
            raise ConfigurationError(
                f"{settings.source}: EDITORS chứa giá trị lạ: {unknown}"
            )
        if len(editor_list) != len(set(editor_list)):
            raise ConfigurationError(f"{settings.source}: EDITORS có giá trị lặp")

    matrix = build_matrix(protocols, chosen, editor_list)
    if not matrix.configurations:
        raise ConfigurationError(
            f"{settings.source}: không còn tổ hợp nào sau khi lọc theo khả năng "
            "của giao thức"
        )

    settings.required_text("SERVER_HOST")
    settings.required_text("SERVER_USER")

    return ExperimentPlan(
        protocols=protocols,
        editors=editor_list,
        scenarios=chosen,
        matrix=matrix,
        trials=settings.integer(
            "TRIALS_PER_COMBINATION", DEFAULT_TRIALS_PER_CONFIGURATION, minimum=1
        ),
        seed=settings.integer("RANDOM_SEED", default_seed),
        result_dir=settings.path("RESULT_DIR", "artifacts/results"),
        run_id=run_id,
        warmup_seconds=settings.number("WARMUP_SECONDS", 5.0, minimum=0.0),
        inter_trial_delay_seconds=settings.number(
            "INTER_TRIAL_DELAY_SECONDS", 3.0, minimum=0.0
        ),
    )
