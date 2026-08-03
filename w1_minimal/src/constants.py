COMMANDS = ("ls", "df -h", "free -m", "ps aux", "uptime")
PROTOCOLS = ("ssh", "ssh3", "mosh")

SAMPLE_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "protocol",
    "loop_index", "warmup", "command_index", "command", "status",
    "latency_ms", "output_bytes", "note",
)

LOOP_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "protocol",
    "loop_index", "warmup", "status", "completed_commands",
    "loop_latency_ms", "note",
)

SETUP_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "protocol",
    "status", "session_setup_ms", "note",
)

ORDER_FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "protocol",
)

