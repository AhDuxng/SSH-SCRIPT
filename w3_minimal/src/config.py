import os
import shlex


# Đọc cấu hình KEY=VALUE và cho phép biến môi trường ghi đè.
def load_env(path: str) -> dict:
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in tuple(env):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


# Trích dẫn một giá trị an toàn để đưa vào lệnh shell.
def q(s: str) -> str:
    return shlex.quote(str(s))


# Tách danh sách cấu hình phân cách bằng dấu phẩy.
def split_csv(value: str) -> list:
    return [p.strip() for p in value.split(",") if p.strip()]


# Tách chuỗi tham số theo cú pháp shell.
def split_args(value: str) -> list:
    return shlex.split(value) if value.strip() else []


# Đọc một cờ boolean từ cấu hình.
def bool_cfg(cfg: dict, name: str, default: str = "0") -> bool:
    return cfg.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# Chuyển lệnh dạng danh sách thành chuỗi phục vụ audit.
def qjoin(cmd) -> str:
    if isinstance(cmd, str):
        return cmd
    return shlex.join([str(part) for part in cmd])
