# Lệnh chạy W1 minimal

Chạy từ thư mục dự án:

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w1_minimal
```

## Cài lần đầu

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
```

Sửa `SERVER_USER`, `SERVER_HOST`, thông tin khóa và endpoint SSH3 trong
`config.env`. File này không được Git theo dõi.

## Chạy đầy đủ

```bash
bash run_w1.sh config.env
```

Mặc định chạy 5 connection độc lập cho mỗi giao thức. Mỗi connection có một
loop warm-up và 10 loop được đưa vào thống kê; mỗi loop luôn chứa đúng năm lệnh.

Smoke test nên dùng thư mục tạm để không ghi đè dữ liệu chính:

```bash
PROTOCOLS=ssh TRIALS_PER_PROTOCOL=1 LOOPS_PER_TRIAL=1 WARMUP_LOOPS=0 \
RESULT_DIR=/private/tmp/w1_results FIGURE_DIR=/private/tmp/w1_figures \
bash run_w1.sh config.env
```

## Chạy lại phân tích và vẽ hình

```bash
.venv/bin/python tools/analyze_w1.py artifacts/results

.venv/bin/python tools/plot_w1.py artifacts/results artifacts/figures --metric mean
.venv/bin/python tools/plot_w1.py artifacts/results artifacts/figures --metric median
.venv/bin/python tools/plot_w1.py artifacts/results artifacts/figures --metric p90
.venv/bin/python tools/plot_w1.py artifacts/results artifacts/figures --metric p95
```

## Kiểm tra code

```bash
PYTHONPYCACHEPREFIX=/private/tmp/w1_pycache \
  .venv/bin/python -m py_compile src/*.py tools/*.py
PYTHONPYCACHEPREFIX=/private/tmp/w1_pycache \
  .venv/bin/python -m unittest discover -s tests -v
bash -n run_w1.sh
```

