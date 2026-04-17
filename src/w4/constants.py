DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]

# Mặc định chỉ đo large_output_latency (+ session_setup luôn được đo nội bộ).
# line_echo KHÔNG nằm mặc định vì:
#   - Chạy cả 2 metric trong 1 trial tốn thêm thời gian.
#   - Hai metric đo độc lập, nên chạy riêng để tránh nhiễu chéo.
# Để đo line_echo: thêm --metrics session_setup large_output_latency line_echo
# Để chỉ đo line_echo: --metrics session_setup line_echo
DEFAULT_METRICS   = ["session_setup", "large_output_latency"]
DEFAULT_PROMPT    = "__W4PROMPT__"
DEFAULT_SSH3_PATH = "/ssh3-term"

W4_COMMANDS = [
    "find /usr -type f 2>/dev/null | head -n 5000",
    "journalctl -n 2000",
    "python3 -c \"print('X' * 100000)\""
]

ANSI_NOISE = (
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"   
    r"|\x1b[@-Z\\-_]"             
    r"|[\x00\x08]"                
    r")*"
)