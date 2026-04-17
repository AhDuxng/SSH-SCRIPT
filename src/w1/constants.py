DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]

# W1 Metrics
DEFAULT_METRICS   = ["session_setup", "command_latency"]
DEFAULT_PROMPT    = "__W1PROMPT__"
DEFAULT_SSH3_PATH = "/ssh3-term"

W1_COMMANDS = [
    "ls",
    "df -h",
    "ps aux",
    "grep root /etc/passwd",
]

ANSI_NOISE = (
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"   
    r"|\x1b[@-Z\\-_]"             
    r"|[\x00\x08]"                
    r")*"
)