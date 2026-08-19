"""Workload definitions and stable CSV schemas for W4."""

PROTOCOLS = ("ssh", "ssh3", "mosh")
EDITORS = ("vim", "nano")
SCENARIOS = ("W4-CMD", "W4-OUTPUT", "W4-MIX")

COMMANDS = (
    "ls -1 /usr/bin | head -n 30",
    "df -h /",
    "free -m",
    "ps -eo pid,comm,%cpu,%mem --sort=pid | head -n 30",
    "uptime",
)

PROBE_BYTES = 100
PROBE_CHARACTERS = 100
PROBE_LINES = 6
PROBE_SHA256 = "13a17464f650cd3d831c1433a226d4895555f56ce8cd52a13f8f3841a0bbd430"

PAYLOAD_NAME = "large_output_s0_1MiB.txt"
PAYLOAD_BYTES = 1_048_576
PAYLOAD_LINE_BYTES = 4_096
PAYLOAD_LINES = 256
PAYLOAD_PREFIX = "W4O0|"
PAYLOAD_SHA256 = "51a7200cd10e343f430ab6acb2b0e67b73adfafe5e07450dbc305d18bdfc2504"

IDENTITY_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "logical_workload_count",
)

ORDER_FIELDS = IDENTITY_FIELDS

KEYSTROKE_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "transport_stream_id", "conversation_stream_id",
    "transport_semantics", "measurement_mode", "char_index", "char_total",
    "source_line", "source_column", "token", "send_ns", "render_ns",
    "latency_ms", "status", "completed", "stall", "timeout",
    "cursor_row", "cursor_column", "render_verification", "note",
)

BACKGROUND_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "workload_type", "transport_stream_id",
    "conversation_stream_id", "transport_semantics", "measurement_origin",
    "sample_index", "operation_index", "operation", "send_time_ns",
    "first_byte_time_ns", "completion_time_ns", "first_byte_latency_ms",
    "completion_latency_ms", "exit_code", "expected_bytes", "received_bytes",
    "expected_sha256", "received_sha256", "completion_marker_received",
    "output_complete", "timed_out", "status", "note",
)

STREAM_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "workload_type", "transport_stream_id",
    "conversation_stream_id", "transport_semantics", "measurement_mode",
    "expected_units", "attempted_units", "completed_units",
    "completion_rate_pct", "stall_count", "stall_rate_pct", "timeout_count",
    "timeout_rate_pct", "complete_outputs", "output_completeness_pct",
    "expected_bytes", "received_bytes", "mean_ms", "median_ms", "p95_ms",
    "p99_ms", "stream_complete", "status", "note",
)

TRIAL_FIELDS = IDENTITY_FIELDS + (
    "connection_valid", "connection_pid", "socket_count",
    "opened_transport_streams", "unique_transport_streams",
    "conversation_count", "ready_workloads", "expected_keystrokes",
    "completed_keystrokes", "keystroke_completion_rate_pct", "stall_count",
    "stall_rate_pct", "timeout_count", "timeout_rate_pct",
    "background_samples", "background_completed_samples",
    "background_completion_rate_pct", "completed_streams",
    "stream_completion_rate_pct", "setup_ms", "workload_elapsed_ms",
    "status", "note",
)

AUDIT_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "workload_type", "connection_valid", "connection_pid",
    "socket_count", "transport_stream_id", "conversation_stream_id",
    "transport_semantics", "note",
)
