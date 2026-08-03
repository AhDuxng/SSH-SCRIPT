# Lệnh chạy W2 minimal

## Cài lần đầu

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w2_minimal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
```

Sửa `SERVER_USER`, `SERVER_HOST`, thông tin khóa, endpoint SSH3 và
`DOCKER_LOGS_COMMAND` trong `config.env`.

## Chạy đầy đủ

```bash
bash run_w2.sh config.env
```

Ví dụ gắn nhãn điều kiện mạng và chạy smoke test không ghi đè dữ liệu chính:

```bash
NETWORK_PROFILE=high_loss PROTOCOLS=ssh WORKLOADS=large_file \
TRIALS_PER_COMBINATION=1 LARGE_FILE_SIZE_BYTES=1048576 \
RESULT_DIR=/private/tmp/w2_results FIGURE_DIR=/private/tmp/w2_figures \
bash run_w2.sh config.env
```

Nếu file đã được chuẩn bị bằng cách khác:

```bash
PREPARE_LARGE_FILE=0 bash run_w2.sh config.env
```

## Phân tích và vẽ lại

```bash
.venv/bin/python tools/analyze_w2.py artifacts/results

.venv/bin/python tools/plot_w2.py artifacts/results artifacts/figures --metric mean
.venv/bin/python tools/plot_w2.py artifacts/results artifacts/figures --metric median
.venv/bin/python tools/plot_w2.py artifacts/results artifacts/figures --metric p90
.venv/bin/python tools/plot_w2.py artifacts/results artifacts/figures --metric p95
```

Mỗi metric sinh bốn hình, mỗi hình có cả PNG và PDF: latency, throughput,
output size và session setup.

## Kiểm tra code

```bash
PYTHONPYCACHEPREFIX=/private/tmp/w2_pycache \
  .venv/bin/python -m py_compile src/*.py tools/*.py
PYTHONPYCACHEPREFIX=/private/tmp/w2_pycache \
  .venv/bin/python -m unittest discover -s tests -v
bash -n run_w2.sh scripts/*.sh
```

