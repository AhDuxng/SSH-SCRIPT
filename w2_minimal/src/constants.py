PROTOCOLS = ("ssh", "ssh3", "mosh")
WORKLOADS = ("find_usr", "docker_logs", "large_file")

DISPLAY_WORKLOAD = {
    "find_usr": "find /usr",
    "docker_logs": "docker logs",
    "large_file": "cat large_file.txt",
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
    "received_bytes", "receive_duration_s", "observed_rate_bytes_per_sec",
    "configured_rate_bytes_per_sec", "configured_chunk_bytes", "failure_stage", "note",
)

ORDER_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload",
)
