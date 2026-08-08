PROTOCOLS = ("ssh", "ssh3", "mosh")
WORKLOADS = ("find_usr", "docker_logs", "large_file")

DISPLAY_WORKLOAD = {
    "find_usr": "find /usr",
    "docker_logs": "docker logs",
    "large_file": "cat large_file.txt",
}

SAMPLE_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "sample_index", "status", "latency_ms",
    "command_exit_code", "start_local_ns", "end_local_ns", "output_bytes",
    "throughput_bytes_per_sec", "completion_semantics", "note",
)

SETUP_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "session_setup_ms", "note",
)

TRIAL_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "expected_samples", "successful_samples",
    "received_bytes", "receive_duration_s", "observed_rate_bytes_per_sec",
    "failure_stage", "note",
)

ORDER_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload",
)
