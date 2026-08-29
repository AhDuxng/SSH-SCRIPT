"""Khả năng của từng giao thức, dùng để quyết định ma trận thí nghiệm.

Đây là nguồn sự thật duy nhất cho câu hỏi "giao thức nào được phép chạy kịch
bản nhiều stream". Không nơi nào khác trong repository được phép kiểm tra tên
giao thức để suy ra điều đó.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolCapability:
    """Mô tả những gì một giao thức thực sự cung cấp ở tầng vận chuyển."""

    name: str
    label: str
    supports_multi_stream: bool
    stream_semantics: str
    rationale: str

    # Số vai trò logic tối đa mà giao thức có thể phục vụ trên một connection.
    def max_concurrent_streams(self, requested: int) -> int:
        return requested if self.supports_multi_stream else 1


# Mosh đồng bộ trạng thái màn hình của đúng một terminal session; nó không có
# khái niệm channel hay stream tương đương SSH channel / QUIC stream. Vì vậy nó
# chỉ được đánh giá ở kịch bản một workload. Mọi kịch bản nhiều stream chỉ áp
# dụng cho SSH và SSH3, nơi phép so sánh multiplexing mới có nghĩa.
CAPABILITIES: dict[str, ProtocolCapability] = {
    "ssh": ProtocolCapability(
        name="ssh",
        label="SSH",
        supports_multi_stream=True,
        stream_semantics="ssh_session_channel",
        rationale="một TCP connection qua ControlMaster, mỗi vai trò là một session channel",
    ),
    "ssh3": ProtocolCapability(
        name="ssh3",
        label="SSH3",
        supports_multi_stream=True,
        stream_semantics="quic_bidirectional_stream",
        rationale="một QUIC connection và một conversation, mỗi vai trò là một bidirectional stream",
    ),
    "mosh": ProtocolCapability(
        name="mosh",
        label="Mosh",
        supports_multi_stream=False,
        stream_semantics="terminal_session",
        rationale=(
            "Mosh đồng bộ trạng thái màn hình của một terminal session và không "
            "cung cấp stream logic tương đương SSH channel hay QUIC stream"
        ),
    ),
}

KNOWN_PROTOCOLS = tuple(CAPABILITIES)


# Tra cứu khả năng của một giao thức.
def capability(protocol: str) -> ProtocolCapability:
    try:
        return CAPABILITIES[protocol]
    except KeyError as exc:
        known = ", ".join(KNOWN_PROTOCOLS)
        raise ValueError(
            f"giao thức không xác định: {protocol!r} (đã biết: {known})"
        ) from exc


# Cho biết một giao thức có chạy được kịch bản với số stream yêu cầu hay không.
def supports_stream_count(protocol: str, stream_count: int) -> bool:
    if stream_count < 1:
        raise ValueError(f"stream_count phải >= 1, nhận {stream_count}")
    return stream_count == 1 or capability(protocol).supports_multi_stream


# Nhãn hiển thị trên hình và bảng tổng hợp.
def label(protocol: str) -> str:
    return capability(protocol).label
