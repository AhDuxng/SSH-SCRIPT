TARGET_USER = "trungnt"
TARGET_HOST = "100.106.17.78"
ITERATIONS = 10
WARMUP_ROUNDS = 2
COMMAND_TIMEOUT_SEC = 30
RANDOM_SEED = 42
OUTPUT_JSON = "w1_results.json"
OUTPUT_CSV = "w1_raw_samples.csv"
COMMANDS = [
    "ls",
    "df -h",
    "ps aux",
    "ps aux | grep root",
]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
