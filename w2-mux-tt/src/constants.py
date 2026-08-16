"""Khai báo workload, payload và schema kết quả W2."""

PAYLOAD_BYTES = 102_400
PAYLOAD_LINE_BYTES = 4_096
PAYLOAD_LINES = PAYLOAD_BYTES // PAYLOAD_LINE_BYTES
PAYLOAD_NAMES = tuple(
    f"large_output_s{index}_100KB.txt" for index in range(4)
)
PAYLOAD_PREFIXES = tuple(f"W2S{index}|" for index in range(4))
PAYLOAD_SHA256 = (
    "574e67a5726d23330a7ce60061b23e43a756ea8c9192df910f332e023eb74d85",
    "08d0af71368751ba7dcc8c715661790c9aec9aba3d437104512fd20710ba7b48",
    "bb894c3f54c1fcf0c98c5b9bb6f9da2ca4b1e190c23ffe7fa4cfd93260bbe798",
    "a8992235b2c46fc84f8c2387c6d9d74be87190d67563a87cdf53ae35e5309f11",
)

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
    "verified_bytes", "verified_byte_ratio_pct", "overrun_bytes",
    "expected_lines", "received_lines",
    "valid_unique_lines", "missing_lines", "duplicate_lines",
    "invalid_lines", "content_coverage_pct",
    "expected_sha256", "received_sha256", "verified_sha256",
    "verification_mode", "throughput_mib_s",
    "completion_marker_received", "bytes_complete", "lines_complete",
    "hash_complete", "raw_capture_exact", "output_complete", "timed_out",
    "status", "note",
)

STREAM_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "stream_index", "transport_stream_id",
    "conversation_stream_id", "payload_name", "expected_transfers",
    "attempted_transfers", "completed_transfers", "partial_transfers",
    "timeout_transfers", "skipped_transfers",
    "transfer_completion_rate_pct", "attempted_transfer_completion_rate_pct",
    "completion_markers_received", "complete_outputs",
    "output_completeness_pct", "mean_content_coverage_pct",
    "mean_verified_byte_ratio_pct", "mean_raw_byte_ratio_pct",
    "byte_verification_rate_pct", "hash_verification_rate_pct",
    "stream_completed",
    "started_time_ns", "completed_time_ns", "elapsed_ms", "note",
)

TRIAL_FIELDS = IDENTITY_FIELDS + (
    "connection_valid", "connection_pid", "socket_count", "opened_streams",
    "unique_transport_streams", "conversation_count", "ready_streams",
    "expected_transfers", "attempted_transfers", "completed_transfers",
    "partial_transfers", "timeout_transfers", "skipped_transfers",
    "transfer_completion_rate_pct", "attempted_transfer_completion_rate_pct",
    "completed_streams",
    "stream_completion_rate_pct", "complete_outputs",
    "output_completeness_pct", "mean_content_coverage_pct",
    "mean_verified_byte_ratio_pct", "mean_raw_byte_ratio_pct",
    "byte_verification_rate_pct", "hash_verification_rate_pct",
    "setup_ms", "workload_elapsed_ms",
    "status", "note",
)

ORDER_FIELDS = IDENTITY_FIELDS

AUDIT_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "transport_stream_id", "conversation_stream_id",
    "connection_valid", "connection_pid", "socket_count",
    "transport_semantics", "note",
)
