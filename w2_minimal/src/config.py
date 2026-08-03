import os
import shlex


# Đọc file KEY=VALUE và cho phép biến môi trường ghi đè cấu hình đã khai báo.
def load_env(path):
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


# Đọc một cờ boolean từ cấu hình môi trường.
def bool_cfg(cfg, key, default="0"):
    return cfg.get(key, default).strip().lower() in ("1", "true", "yes", "on")


# Tách danh sách cấu hình phân cách bằng dấu phẩy.
def split_csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


# Trích dẫn một giá trị để ghép an toàn vào lệnh shell trên target.
def q(value):
    return shlex.quote(str(value))

