import os


# Đọc tệp env và áp dụng giá trị ghi đè từ môi trường.
def load_env(path: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"').strip("'")
    for key in tuple(cfg):
        if key in os.environ:
            cfg[key] = os.environ[key]
    for key in (
        "RUN_ID", "CONGESTION_LOG_DIR", "SERVER_CONGESTION_LOG_DIR",
        "REMOTE_CONGESTION_SAMPLER",
    ):
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg


# Tách danh sách cấu hình phân cách bằng dấu phẩy.
def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
