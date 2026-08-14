"""Khai báo workload, payload và schema kết quả W2."""

PAYLOAD_BYTES = 102_400
PAYLOAD_LINE_BYTES = 128
PAYLOAD_LINES = PAYLOAD_BYTES // PAYLOAD_LINE_BYTES
PAYLOAD_NAMES = tuple(
    f"large_output_s{index}_100KiB.txt" for index in range(4)
)
PAYLOAD_PREFIXES = tuple(f"W2S{index}|" for index in range(4))

SCENARIOS = {"W2-S1": 1, "W2-S2": 2, "W2-S4": 4}
PROTOCOLS = ("ssh", "ssh3", "mosh")

IDENTITY_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "scenario", "stream_count",
)

TRANSFER_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "stream_index", "transport_stream_id",
    "conversation_stream_id", "payload_name", "remote_payload_path",
    "sample_index", "request_id", "send_time_ns", "first_byte_time_ns",
    "last_byte_time_ns", "marker_time_ns", "first_byte_latency_ms",
    "completion_latency_ms", "marker_latency_ms", "exit_code",
    "expected_bytes", "received_bytes", "raw_byte_ratio_pct",
    "overrun_bytes", "expected_lines", "received_lines",
    "valid_unique_lines", "missing_lines", "duplicate_lines",
    "invalid_lines", "content_coverage_pct",
    "expected_sha256", "received_sha256", "throughput_mib_s",
    "completion_marker_received", "bytes_complete", "lines_complete",
    "hash_complete", "output_complete", "timed_out", "status", "note",
)

STREAM_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "stream_index", "transport_stream_id",
    "conversation_stream_id", "payload_name", "expected_transfers",
    "completed_transfers", "transfer_completion_rate_pct",
    "completion_markers_received", "complete_outputs",
    "output_completeness_pct", "mean_content_coverage_pct",
    "mean_raw_byte_ratio_pct", "stream_completed",
    "started_time_ns", "completed_time_ns", "elapsed_ms", "note",
)

TRIAL_FIELDS = IDENTITY_FIELDS + (
    "connection_valid", "connection_pid", "socket_count", "opened_streams",
    "unique_transport_streams", "conversation_count", "ready_streams",
    "expected_transfers", "completed_transfers",
    "transfer_completion_rate_pct", "completed_streams",
    "stream_completion_rate_pct", "complete_outputs",
    "output_completeness_pct", "mean_content_coverage_pct",
    "mean_raw_byte_ratio_pct",
    "setup_ms", "workload_elapsed_ms",
    "status", "note",
)

ORDER_FIELDS = IDENTITY_FIELDS

AUDIT_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "transport_stream_id", "conversation_stream_id",
    "connection_valid", "connection_pid", "socket_count",
    "transport_semantics", "note",
)
