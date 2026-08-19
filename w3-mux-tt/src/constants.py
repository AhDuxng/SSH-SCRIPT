"""Hằng số và schema CSV của W3."""

PROTOCOLS = ("ssh", "ssh3", "mosh")
EDITORS = ("vim", "nano")
SCENARIO_STREAMS = {"W3-I1": 1, "W3-I2": 2, "W3-I4": 4}
PROBE_BYTES = 100
PROBE_CHARACTERS = 100
PROBE_LINES = 6
PROBE_SHA256 = "13a17464f650cd3d831c1433a226d4895555f56ce8cd52a13f8f3841a0bbd430"

ORDER_FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "stream_count",
]

KEYSTROKE_FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "stream_count", "stream_role",
    "transport_stream_id", "conversation_stream_id", "transport_semantics",
    "measurement_mode", "sample_index", "char_index", "char_total",
    "source_offset", "source_char_total", "source_line", "source_column", "token",
    "send_ns", "render_ns", "latency_ms", "status", "completed", "stall",
    "timeout", "cursor_row", "cursor_column", "render_verification", "note",
]

STREAM_FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "stream_count", "stream_role",
    "transport_stream_id", "conversation_stream_id", "transport_semantics",
    "measurement_mode", "expected_keystrokes", "completed_keystrokes",
    "keystroke_completion_rate_pct", "stall_count", "stall_rate_pct",
    "timeout_count", "timeout_rate_pct", "mean_ms", "median_ms", "p95_ms",
    "p99_ms", "stream_complete", "status", "note",
]

TRIAL_FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "stream_count", "connection_valid",
    "connection_pid", "socket_count", "opened_transport_streams",
    "unique_transport_streams", "conversation_count", "ready_streams",
    "expected_keystrokes", "completed_keystrokes",
    "keystroke_completion_rate_pct", "stall_count", "stall_rate_pct",
    "timeout_count", "timeout_rate_pct", "completed_streams",
    "stream_completion_rate_pct", "setup_ms", "workload_elapsed_ms",
    "status", "note",
]

AUDIT_FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "editor", "scenario", "stream_count", "stream_role",
    "connection_valid", "connection_pid", "socket_count",
    "transport_stream_id", "conversation_stream_id", "transport_semantics", "note",
]
