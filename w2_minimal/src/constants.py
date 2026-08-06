PROTOCOLS = ("ssh", "ssh3", "mosh")
WORKLOADS = ("top", "tail", "ping")

DISPLAY_WORKLOAD = {
    "top": "top-like monitor",
    "tail": "tail -f",
    "ping": "ping -D",
}

SAMPLE_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "sample_index", "remote_sequence", "status",
    "latency_ms", "remote_event_ns", "recv_local_ns", "note",
)

SETUP_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "session_setup_ms", "note",
)

CLOCK_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "requested_probes", "valid_probes",
    "clock_offset_ns", "clock_offset_ms", "median_rtt_ms", "method", "note",
)

TRIAL_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "expected_samples", "successful_samples",
    "failure_stage", "note",
)

ORDER_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload",
)
