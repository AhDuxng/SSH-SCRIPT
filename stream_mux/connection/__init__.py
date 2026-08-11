"""Khởi tạo connection từ đặc tả do workload truyền vào."""

from .base import (
    ConnectionAudit,
    MultiplexConnection,
    RawStream,
    StreamEvent,
    StreamSpec,
)
from .mosh import MoshConnection
from .ssh import SSHConnection
from .ssh3 import SSH3Connection


# Chọn transport và tạo một connection multiplex.
def open_multiplex_connection(
    cfg: dict, protocol: str, specs: list[StreamSpec], trial_tag: str
) -> MultiplexConnection:
    factories = {
        "ssh": SSHConnection,
        "ssh3": SSH3Connection,
        "mosh": MoshConnection,
    }
    try:
        factory = factories[protocol]
    except KeyError as exc:
        raise ValueError(f"unsupported protocol: {protocol}") from exc
    return factory(cfg, specs, trial_tag)


__all__ = [
    "ConnectionAudit",
    "MultiplexConnection",
    "RawStream",
    "StreamEvent",
    "StreamSpec",
    "MoshConnection",
    "SSHConnection",
    "SSH3Connection",
    "open_multiplex_connection",
]
