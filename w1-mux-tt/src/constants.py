COMMANDS = (
    "ls -1 /usr/bin | head -n 30",
    "df -h /",
    "free -m",
    "ps -eo pid,comm,%cpu,%mem --sort=pid | head -n 30",
    "uptime",
)

SCENARIOS = {"W1-S1": 1, "W1-S2": 2, "W1-S4": 4}
PROTOCOLS = ("ssh", "ssh3", "mosh")

IDENTITY_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "scenario", "stream_count",
)

SAMPLE_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "stream_index", "transport_stream_id",
    "conversation_stream_id", "sample_index", "cycle_index",
    "command_index", "command", "request_id",
    "send_time_ns", "completion_time_ns", "latency_ms", "exit_code",
    "expected_bytes", "received_bytes", "expected_sha256", "received_sha256",
    "completion_marker_received", "output_verifiable", "output_complete",
    "timed_out", "status", "stderr_bytes", "note",
)

STREAM_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "stream_index", "transport_stream_id",
    "conversation_stream_id", "expected_commands", "completed_commands",
    "command_completion_rate_pct", "complete_outputs", "output_completeness_pct",
    "stream_completed", "started_time_ns", "completed_time_ns", "elapsed_ms", "note",
)

TRIAL_FIELDS = IDENTITY_FIELDS + (
    "connection_valid", "connection_pid", "socket_count", "opened_streams",
    "unique_transport_streams", "conversation_count", "ready_streams",
    "expected_commands", "completed_commands", "command_completion_rate_pct",
    "completed_streams", "stream_completion_rate_pct", "complete_outputs",
    "output_completeness_pct", "setup_ms", "workload_elapsed_ms", "status", "note",
)

ORDER_FIELDS = IDENTITY_FIELDS

AUDIT_FIELDS = IDENTITY_FIELDS + (
    "stream_role", "transport_stream_id", "conversation_stream_id",
    "connection_valid", "connection_pid", "socket_count",
    "transport_semantics", "note",
)
