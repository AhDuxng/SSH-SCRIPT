PROTOCOLS = ("ssh", "ssh3", "mosh")
WORKLOADS = ("find_usr", "docker_logs", "journalctl", "large_file")

DISPLAY_WORKLOAD = {
    "find_usr": "find /usr",
    "docker_logs": "docker logs",
    "journalctl": "journalctl",
    "large_file": "cat large_file.txt",
}

SAMPLE_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "command", "status", "latency_ms",
    "output_bytes", "output_lines", "throughput_mib_s", "exit_code", "note",
)

SETUP_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "status", "session_setup_ms", "note",
)

ORDER_FIELDS = (
    "run_id", "network_profile", "block_id", "trial_order", "trial_id",
    "protocol", "workload", "command",
)

