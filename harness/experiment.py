"""Sinh ma trận thí nghiệm và lịch chạy dùng chung cho W1–W4.

Toàn bộ quyết định "tổ hợp nào được chạy" và "chạy bao nhiêu lần" tập trung ở
đây. Các workload chỉ khai báo kịch bản của mình; việc loại bỏ tổ hợp không hợp
lệ do `capability` quyết định chứ không phải bằng cách kiểm tra tên giao thức
rải rác trong code.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from stream_mux.capability import capability, supports_stream_count

# Số lần lặp cho mỗi cấu hình, dùng chung cho mọi workload. Đây là giá trị mặc
# định duy nhất trong repository; config.env chỉ được phép ghi đè có chủ đích.
DEFAULT_TRIALS_PER_CONFIGURATION = 5


@dataclass(frozen=True)
class Scenario:
    """Kịch bản của workload; đặt measures_multiplexing=False cho kịch bản đo can nhiễu."""

    name: str
    stream_count: int
    measures_multiplexing: bool = True

    def __post_init__(self) -> None:
        if self.stream_count < 1:
            raise ValueError(f"{self.name}: stream_count phải >= 1")


@dataclass(frozen=True)
class Configuration:
    """Một ô trong ma trận thí nghiệm."""

    protocol: str
    scenario: Scenario
    editor: str = ""

    # Số vai trò thực sự mở được; giao thức không multiplex luôn là 1.
    @property
    def stream_count(self) -> int:
        return capability(self.protocol).max_concurrent_streams(
            self.scenario.stream_count
        )

    # Mã định danh ổn định của một trial trong một block.
    def trial_id(self, block_id: int) -> str:
        parts = [self.protocol]
        if self.editor:
            parts.append(self.editor)
        parts.append(self.scenario.name.lower())
        return "_".join(parts) + f"_r{block_id:02d}"


@dataclass(frozen=True)
class SkippedConfiguration:
    """Một ô bị loại khỏi ma trận, kèm lý do có thể in ra cho người đọc."""

    protocol: str
    scenario: Scenario
    editor: str
    reason: str


@dataclass(frozen=True)
class ExperimentMatrix:
    """Ma trận đã được lọc, cùng danh sách những gì bị loại và vì sao."""

    configurations: tuple[Configuration, ...]
    skipped: tuple[SkippedConfiguration, ...] = field(default=())

    def __len__(self) -> int:
        return len(self.configurations)

    # Các giao thức thực sự xuất hiện trong ma trận.
    def protocols(self) -> tuple[str, ...]:
        seen = []
        for item in self.configurations:
            if item.protocol not in seen:
                seen.append(item.protocol)
        return tuple(seen)

    # Các kịch bản mà một giao thức được chạy.
    def scenarios_for(self, protocol: str) -> tuple[str, ...]:
        return tuple(
            item.scenario.name
            for item in self.configurations
            if item.protocol == protocol
        )


# Lọc ma trận theo khả năng thật của từng giao thức; loại trước khi chạy
# nên thí nghiệm không hỏng giữa chừng vì một tổ hợp không hợp lệ.
def build_matrix(
    protocols, scenarios, editors=(),
) -> ExperimentMatrix:
    editor_list = tuple(editors) or ("",)
    kept: list[Configuration] = []
    skipped: list[SkippedConfiguration] = []
    for protocol in protocols:
        detail = capability(protocol)
        for editor in editor_list:
            for scenario in scenarios:
                needs_multi = (
                    scenario.measures_multiplexing and scenario.stream_count > 1
                )
                if needs_multi and not supports_stream_count(
                    protocol, scenario.stream_count
                ):
                    skipped.append(SkippedConfiguration(
                        protocol, scenario, editor, detail.rationale,
                    ))
                    continue
                kept.append(Configuration(protocol, scenario, editor))
    return ExperimentMatrix(tuple(kept), tuple(skipped))


# Sinh lịch randomized complete blocks; hạt giống dẫn xuất từ seed và
# block_id nên lịch tái lập được.
def build_schedule(
    matrix: ExperimentMatrix, trials: int, seed: int, run_id: str,
) -> list[dict]:
    if trials < 1:
        raise ValueError(f"trials phải >= 1, nhận {trials}")
    if not matrix.configurations:
        raise ValueError("ma trận thí nghiệm rỗng")

    schedule: list[dict] = []
    order = 0
    for block_id in range(1, trials + 1):
        block = list(matrix.configurations)
        random.Random(seed + block_id).shuffle(block)
        for item in block:
            order += 1
            trial_id = item.trial_id(block_id)
            # logical_workload_count: vai trò do kịch bản định nghĩa.
            # stream_count: stream transport thực mở, = 1 nếu không multiplex.
            entry = {
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": trial_id,
                "trial_tag": f"o{order:04d}_{trial_id}",
                "protocol": item.protocol,
                "scenario": item.scenario.name,
                "stream_count": item.stream_count,
                "logical_workload_count": item.scenario.stream_count,
            }
            if item.editor:
                entry["editor"] = item.editor
            schedule.append(entry)
    return schedule


# Vẽ bảng ma trận để in ra trước khi chạy.
def render_matrix(matrix: ExperimentMatrix, scenarios, trials: int) -> str:
    names = [scenario.name for scenario in scenarios]
    width = max((len(name) for name in names), default=4) + 2
    lines = ["", "Ma trận thí nghiệm:", ""]
    header = " " * 8 + "".join(f"{name:^{width}}" for name in names)
    lines.append(header)
    for protocol in matrix.protocols():
        allowed = set(matrix.scenarios_for(protocol))
        cells = "".join(
            f"{'✓' if name in allowed else '—':^{width}}" for name in names
        )
        lines.append(f"{protocol:<8}{cells}")
    lines.append("")
    lines.append(f"Số trial cho mỗi cấu hình: {trials}")
    lines.append(f"Tổng số trial: {len(matrix) * trials}")
    if matrix.skipped:
        lines.append("")
        lines.append("Tổ hợp bị loại:")
        seen = set()
        for item in matrix.skipped:
            key = (item.protocol, item.scenario.name)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  {item.protocol} × {item.scenario.name}: {item.reason}")
    return "\n".join(lines)
