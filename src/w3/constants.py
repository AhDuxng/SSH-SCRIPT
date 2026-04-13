DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_METRICS   = ["session_setup", "keystroke_latency"]
DEFAULT_PROMPT    = "__W3PROMPT__"
DEFAULT_SSH3_PATH = "/ssh3-term"

ANSI_NOISE = (
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"   
    r"|\x1b[@-Z\\-_]"             
    r"|[\x00\x08]"                
    r")*"
)