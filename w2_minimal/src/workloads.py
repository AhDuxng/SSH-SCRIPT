from config import q
from constants import WORKLOADS


# Ánh xạ tên workload ổn định sang lệnh shell thực thi trên target.
def workload_commands(cfg):
    commands = {
        "find_usr": cfg.get("FIND_COMMAND", "find /usr"),
        "docker_logs": cfg.get(
            "DOCKER_LOGS_COMMAND", "docker logs $(docker ps -q | head -n 1)"
        ),
        "journalctl": cfg.get("JOURNALCTL_COMMAND", "journalctl --no-pager"),
        "large_file": f"cat {q(cfg.get('LARGE_FILE_PATH', '/tmp/w2_large_file.txt'))}",
    }
    missing = sorted(set(WORKLOADS) - set(commands))
    if missing:
        raise ValueError(f"missing workload commands: {missing}")
    return commands


# Bọc workload bằng redirect stderr và marker chứa exit code thật của lệnh.
def wrap_command(command, marker, max_output_lines=0):
    producer = f"{{ {command}; }} 2>&1"
    if max_output_lines > 0:
        producer = f"{producer} | head -n {int(max_output_lines)}"
    return (
        f"{producer}; w2_rc=$?; "
        f"printf '\\n%s exit_code=%d\\n' {q(marker)} \"$w2_rc\""
    )

