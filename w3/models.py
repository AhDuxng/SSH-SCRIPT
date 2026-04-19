from dataclasses import dataclass
from typing import Optional


@dataclass
class SampleRecord:
    protocol:   str
    metric:     str
    trial_id:   int
    sample_id:  int
    is_warmup:  bool
    token:      str
    latency_ms: float


@dataclass
class FailureRecord:
    protocol:      str
    metric:        str
    trial_id:      int
    sample_id:     int
    is_warmup:     bool
    error_type:    str
    error_message: str


@dataclass
class SummaryRow:
    protocol:           str
    metric:             str
    n:                  int
    failures:           int
    success_rate_pct:   float
    min_ms:             Optional[float]
    mean_ms:            Optional[float]
    median_ms:          Optional[float]
    stdev_ms:           Optional[float]
    p95_ms:             Optional[float]
    p99_ms:             Optional[float]
    max_ms:             Optional[float]
    ci95_half_width_ms: Optional[float]


@dataclass
class RemoteMeta:
    kernel:         str = "unknown"
    mosh_version:   str = "unknown"
    ssh_version:    str = "unknown"
    ssh3_version:   str = "unknown"
    python_version: str = "unknown"