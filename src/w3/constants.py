DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
# session_setup luôn được đo (dùng để chẩn đoán) dù không có trong --metrics.
# line_echo và keystroke_latency đo per-sample latency bên trong mỗi session.
DEFAULT_METRICS   = ["session_setup", "keystroke_latency", "line_echo"]
DEFAULT_PROMPT    = "__W3PROMPT__"
DEFAULT_SSH3_PATH = "/ssh3-term"

ANSI_NOISE = (
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"   
    r"|\x1b[@-Z\\-_]"             
    r"|[\x00\x08]"                
    r")*"
)