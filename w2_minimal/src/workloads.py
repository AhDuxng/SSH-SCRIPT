import shlex


# Ánh xạ ba workload W2 sang lệnh chạy trên target.
def workload_commands(cfg):
    return {
        "find_usr": cfg.get("FIND_COMMAND", "find /usr"),
        "docker_logs": cfg.get("DOCKER_LOGS_COMMAND", "docker logs w2-log-source"),
        "large_file": f"cat {shlex.quote(cfg.get('LARGE_FILE_PATH', '/tmp/w2_large_file.txt'))}",
    }
