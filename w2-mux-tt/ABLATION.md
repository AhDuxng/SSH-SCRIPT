# Cô lập nguyên nhân chênh lệch SSH3 vs SSH trong W2

## Vấn đề cần trả lời

Trong các lần chạy hiện có, SSH3 chậm hơn SSH và mức chênh lệch phụ thuộc mạnh
vào tỷ lệ mất gói:

| Profile | SSH3/SSH (Reno, `pi_runs`) | SSH3/SSH (CUBIC, `pi_runs_2`) |
|---|---:|---:|
| low, W2-S1 | 1.009× | 1.061× |
| low, W2-S4 | 1.179× | 1.177× |
| medium, W2-S1 | 3.021× | 3.589× |
| medium, W2-S4 | 3.515× | 4.442× |
| high, W2-S1 | 1.216× | 1.467× |
| high, W2-S4 | 1.539× | 1.808× |

Hai quan sát định hướng toàn bộ phần điều tra này:

1. **Ở `low` (loss 0%) SSH3 gần bằng SSH** với một stream (1.01×). Một transfer
   100 KiB ở RTT 40 ms kết thúc trong slow start, nơi CUBIC và Reno hoạt động
   giống hệt nhau — nên việc đổi thuật toán không đổi kết quả là điều đã dự đoán
   được, không phải bằng chứng cho thấy transport không có vấn đề.
2. **Chuyển sang CUBIC làm SSH3 chậm thêm 18–26% dưới loss**, trong khi SSH gần
   như không đổi (medium W2-S1: 259.6 → 260.5 ms). Nếu congestion control là nút
   thắt, một thuật toán hiện đại hơn phải cải thiện chứ không làm xấu đi.

Kết luận sơ bộ: chênh lệch không do congestion control. Nhưng trước khi quy cho
bản thân giao thức, phải loại trừ hai đặc điểm của **bàn thí nghiệm** vốn thiên
vị TCP.

## Hai biến gây nhiễu cần loại trừ

### V1 — `netem loss` rơi theo gói, còn QUIC dùng gói nhỏ hơn

`quic-go` gửi datagram cỡ `InitialPacketSizeIPv4 = 1252` byte
(`internal/protocol/params.go`), còn lại khoảng 1215–1221 byte payload sau
header và AEAD tag. TCP trên MTU 1500 dùng MSS 1448–1460 byte.

Cùng một payload 102 400 byte:

```text
TCP  : 102400 / 1448 ≈ 71 gói
QUIC : 102400 / 1215 ≈ 85 gói      → nhiều hơn ~20%
```

`tc netem loss 1.5%` rơi **theo gói**, nên SSH3 hứng nhiều loss event hơn SSH
khoảng 20% cho cùng khối lượng dữ liệu. Đây là tính chất của cách dựng thí
nghiệm, không phải của giao thức.

### V2 — buffer 400 ms ưu ái bên gửi theo burst

`set_network.sh` dùng `tbf ... latency 400ms`, tạo một hàng đợi rất sâu. TCP xả
burst tự do và lấp đầy buffer đó; `quic-go` pace dữ liệu với burst tối đa
`maxBurstSizePackets = 10` (`internal/congestion/pacer.go`). Buffer càng sâu,
lợi thế của bên burst càng lớn.

Một yếu tố thứ ba không loại trừ được bằng cấu hình mạng: PTO của QUIC cộng thêm
`MaxAckDelay = 25 ms` theo RFC 9002, còn RTO của TCP thì không. Phần này là khác
biệt thật giữa hai giao thức và nên được báo cáo như vậy.

## Quy trình chạy

`set_network.sh` được giữ nguyên; các biến ablation được áp thêm bằng lệnh `tc`
và `iptables` chạy riêng **trên cả client và server**. Nếu chỉ áp một đầu, MSS
và độ sâu buffer sẽ bất đối xứng và kết quả vô nghĩa.

Dùng `medium` vì đó là nơi chênh lệch lớn nhất (3.6–4.4×) và do đó dễ đo tác
động nhất. Cố định số trial cho cả bốn cấu hình:

```bash
export ABLATION_ENV="TRIALS_PER_COMBINATION=5 SAMPLES_PER_STREAM_PER_TRIAL=100 \
PROTOCOLS=ssh3,ssh SCENARIOS=W2-S1,W2-S2,W2-S4"
```

Bỏ `mosh` khỏi `PROTOCOLS`: phần ablation này chỉ so sánh SSH3 với SSH.

Hai hàm phụ dưới đây gói các lệnh cần lặp lại; dán vào shell trên **cả hai máy**:

```bash
mss_clamp() {   # mss_clamp 1215  |  mss_clamp off
  sudo iptables -t mangle -D OUTPUT -p tcp --tcp-flags SYN,RST SYN \
    -j TCPMSS --set-mss "${MSS_LAST:-1215}" 2>/dev/null || true
  [[ "$1" == "off" ]] && { unset MSS_LAST; return; }
  sudo iptables -t mangle -A OUTPUT -p tcp --tcp-flags SYN,RST SYN \
    -j TCPMSS --set-mss "$1"
  export MSS_LAST="$1"
}

tbf_latency() {  # tbf_latency eth0 50ms
  sudo tc qdisc change dev "$1" root handle 1: tbf rate 40mbit burst 32kbit latency "$2"
}
```

### A. Baseline — lặp lại điều kiện hiện tại

```bash
# [client] và [server]
sudo ./set_network.sh eth0 medium

# [client]
cd ~/SSH-SCRIPT/w2-mux-tt
env $ABLATION_ENV RESULT_DIR=artifacts/abl-baseline bash run_w2.sh config.env
```

### B. Loại V1 — cho TCP và QUIC cùng số byte payload mỗi gói

```bash
# [client] và [server]
sudo ./set_network.sh eth0 medium
mss_clamp 1215

# [client]
env $ABLATION_ENV RESULT_DIR=artifacts/abl-mss bash run_w2.sh config.env
```

Xác nhận clamp đã có hiệu lực trước khi đo:

```bash
sudo iptables -t mangle -S OUTPUT | grep TCPMSS
sudo tcpdump -i eth0 -c 20 -nn 'tcp[tcpflags] & tcp-syn != 0' -v 2>&1 \
  | grep -o 'mss [0-9]*'
```

### C. Loại V2 — giảm độ sâu buffer

```bash
# [client] và [server]
sudo ./set_network.sh eth0 medium
mss_clamp off
tbf_latency eth0 50ms

# [client]
env $ABLATION_ENV RESULT_DIR=artifacts/abl-shallow bash run_w2.sh config.env
```

### D. Loại cả hai

```bash
# [client] và [server]
sudo ./set_network.sh eth0 medium
mss_clamp 1215
tbf_latency eth0 50ms

# [client]
env $ABLATION_ENV RESULT_DIR=artifacts/abl-both bash run_w2.sh config.env
```

Dọn dẹp sau khi xong, trên cả hai máy:

```bash
mss_clamp off
sudo ./set_network.sh eth0 clear
```

## Đọc kết quả

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
.venv/bin/python tools/compare_runs.py \
  baseline=artifacts/abl-baseline \
  mss1215=artifacts/abl-mss \
  shallow50=artifacts/abl-shallow \
  both=artifacts/abl-both
```

Cột `vs base` là tỷ số `ssh3/ssh` của cấu hình đó chia cho `ssh3/ssh` của
baseline:

- `vs base ≈ 1.0` → biến vừa gỡ bỏ **không** giải thích được chênh lệch.
- `vs base < 1.0` → biến đó chiếm phần tương ứng trong chênh lệch. Ví dụ
  `mss1215` cho `vs base = 0.55` nghĩa là khoảng 45% chênh lệch đến từ cách
  `netem` đếm gói chứ không phải từ giao thức.
- Phần còn lại sau cấu hình `both` là chênh lệch thuộc về chính SSH3/quic-go
  (loss recovery, PTO cộng `MaxAckDelay`, xử lý userspace).

Công cụ tự cảnh báo khi một nhóm có completion < 99%, vì latency chỉ được tính
trên các transfer `completed` và tỷ số sẽ bị lệch do survivorship bias.

## Đếm gói trực tiếp (kiểm chứng V1)

Cách rẻ và không cần thay đổi code: đọc bộ đếm của qdisc trước và sau một trial.

```bash
# [client]
read_pkts() { tc -s qdisc show dev eth0 | awk '/Sent/ {print $4; exit}'; }

before=$(read_pkts)
env $ABLATION_ENV PROTOCOLS=ssh SCENARIOS=W2-S1 TRIALS_PER_COMBINATION=1 \
  RESULT_DIR=/tmp/w2-count-ssh bash run_w2.sh config.env >/dev/null
ssh_pkts=$(( $(read_pkts) - before ))

before=$(read_pkts)
env $ABLATION_ENV PROTOCOLS=ssh3 SCENARIOS=W2-S1 TRIALS_PER_COMBINATION=1 \
  RESULT_DIR=/tmp/w2-count-ssh3 bash run_w2.sh config.env >/dev/null
ssh3_pkts=$(( $(read_pkts) - before ))

echo "ssh=$ssh_pkts ssh3=$ssh3_pkts ratio=$(awk "BEGIN{print $ssh3_pkts/$ssh_pkts}")"
```

Bộ đếm này tính cả chiều gửi của client (chủ yếu là ACK), nên tỷ số không phải
bằng chứng tuyệt đối; nếu cần con số sạch, chạy cùng phép đo trên server, nơi
toàn bộ payload đi ra.

## Xác nhận binary đang dùng thuật toán nào

### Bên nào quyết định congestion control của W2

Trong QUIC, mỗi đầu có congestion controller riêng cho dữ liệu **nó gửi**. W2
cho `sed` chạy trên máy đích và đẩy 100 KiB về client, nên luồng được đo do
**congestion controller của server** điều khiển. Client chỉ gửi vài trăm byte
lệnh.

Hệ quả: `run_wN.sh` chỉ tự động build lại **client** (`build_ssh3_mux.sh`) và
không bao giờ đụng tới server. Nếu chỉ client được build với CUBIC còn server
vẫn là binary cũ, thì W2 vẫn đang đo Reno bất kể client dùng gì.

### Cách kiểm tra một binary bất kỳ

`go mod edit -replace` trỏ quic-go sang bản đã vá nằm trong `.build/`, và dấu
vết đó nằm lại trong metadata của binary:

```bash
go version -m ../stream_mux/bin/ssh3-mux-stdio | grep -A1 'quic-go/quic-go'
```

```text
# CUBIC — có dòng replace trỏ tới cây nguồn đã vá
	dep	github.com/quic-go/quic-go	v0.40.1-0.20240102075208-1083d1fb8f98
	=>	/…/stream_mux/.build/quic-go-cubic-1083d1fb8f98-…	(devel)

# Reno — chỉ có module gốc lấy từ proxy, không có dòng "=>"
	dep	github.com/quic-go/quic-go	v0.40.1-0.20240102075208-1083d1fb8f98	h1:XSdekoU+…
```

Chạy đúng lệnh đó trên server với binary đang phục vụ:

```bash
go version -m /usr/local/bin/ssh3-server | grep -A1 'quic-go/quic-go'
```

`build_ssh3_mux.sh` và `build_ssh3_server.sh` cũng ghi một tệp tóm tắt cạnh
binary, nhưng chỉ từ commit `2d3e436` (25/08/2026) trở đi:

```bash
cat ../stream_mux/bin/ssh3-mux-stdio.build-info
# ssh3_commit=...
# quic_go_version=v0.40.1-0.20240102075208-1083d1fb8f98
# cc_algorithm=reno        # hoặc cubic, theo SSH3_CC
# patch_hash=...
```

Binary không có `.build-info` chắc chắn được build trước khi patch CUBIC tồn
tại. Nhưng `.build-info` có mặt chỉ chứng minh script đã chạy — `go version -m`
mới là bằng chứng đọc trực tiếp từ binary.

### Build và triển khai server

```bash
# [server]
cd ~/SSH-SCRIPT
bash stream_mux/scripts/build_ssh3_server.sh
go version -m stream_mux/bin/ssh3-server-cubic | grep -A1 'quic-go/quic-go'

sudo install -m 0755 stream_mux/bin/ssh3-server-cubic /usr/local/bin/ssh3-server
sudo systemctl restart ssh3-server
sudo systemctl status ssh3-server --no-pager | head -5
```

Client và server phải được build từ cùng một `patch_hash`; nếu chỉ một bên đổi,
kết quả không so sánh được với lần chạy trước.

## Đổi thuật toán chống tắc nghẽn

`SSH3_CC` trong `config.env` chọn thuật toán cho quic-go; mặc định là `reno`.

```bash
SSH3_CC=reno   bash run_w2.sh config.env    # quic-go gốc
SSH3_CC=cubic  bash run_w2.sh config.env    # áp patches/quic_go_cubic.patch
```

Giá trị này nằm trong mã băm patch (`scripts/patch_hash.sh`), nên đổi nó sẽ
tự kích hoạt build lại binary — không thể vô tình đo bằng binary của thuật
toán trước. `metadata.json` ghi cả `congestion_control_requested` (điều bạn
yêu cầu) lẫn `transport_provenance.*.congestion_control` (điều thực sự nằm
trong binary); hai giá trị lệch nhau nghĩa là lần chạy đó không hợp lệ.

Server phải được build lại cùng thuật toán, nếu không hai đầu sẽ khác nhau:

```bash
SSH3_CC=reno bash stream_mux/scripts/build_ssh3_server.sh
sudo install -m 0755 stream_mux/bin/ssh3-server /usr/local/bin/ssh3-server
sudo systemctl restart ssh3-server
```

`preflight.py` so thuật toán của cả client lẫn server với `SSH3_CC` và báo
FAIL nếu lệch.

## Bộ đếm mạng

Mỗi trial chụp bộ đếm `tc -s qdisc` và `/proc/net/{snmp,netstat}` ở cả hai
đầu, **trước và sau** trial nên nằm ngoài khoảng đo. Kết quả ghi vào
`network_counters.csv`, và `tools/analyze_counters.py` quy đổi thành
`network_summary.csv`.

Ba cột trả lời trực tiếp ba giả thuyết về chênh lệch SSH vs SSH3:

| Cột | Trả lời câu hỏi |
|---|---|
| `mean_wire_packet_bytes`, `wire_packets` | QUIC dùng gói 1252 B so với TCP MSS ~1448 B — đo được, không phải suy đoán |
| `wire_bytes_per_payload_byte` | chi phí phát lại thực tế của từng giao thức |
| `tcp_dsack_recv`, `tcp_spurious_rto` | số lần Linux TCP phát hiện mất gói giả do jitter đảo thứ tự và hoàn tác — quic-go v0.40 không có cơ chế tương đương |

Tắt bằng `COLLECT_NETWORK_COUNTERS=0`; đổi interface bằng `NETEM_IFACE`.

## qlog: nhìn thẳng vào cwnd của quic-go

`patches/ssh3_qlog.patch` thêm package `qlogenv` vào ssh3 và gắn tracer vào
`quic.Config` của **cả** client (`cmd/ssh3.go`) lẫn server
(`cmd/ssh3-server.go`). Khi `QLOGDIR` rỗng, `Tracer` là `nil` và quic-go
không tốn gì.

> Không patch được vào chính quic-go: `qlog/event.go` import ngược package
> `quic` để dùng các kiểu lỗi, nên thêm `import qlog` vào `config.go` tạo
> vòng import. Patch phải nằm ở ssh3.

**Bật cho lần chạy chẩn đoán, không bao giờ cho lần chạy lấy số liệu chính** —
qlog ghi khoảng 0.5 MB cho mỗi MB dữ liệu, và ghi thẻ SD trong lúc đo sẽ tự
làm chậm phép đo:

```bash
SSH3_QLOG=1 SSH3_QLOG_DIR=artifacts/qlog RESULT_DIR=artifacts/qlogrun \
  TRIALS_PER_COMBINATION=1 PROTOCOLS=ssh3 bash run_w2.sh config.env
```

### Phía nào mới quan trọng

Trong W2, bên **gửi** 100 KiB là **server** (`cat` tệp payload). Cửa sổ tắc
nghẽn cần nhìn vì thế nằm ở qlog của `ssh3-server`, không phải của client.
qlog phía client chỉ cho biết nó nhận và ACK ra sao.

Bật qlog cho service trên server:

```bash
sudo mkdir -p /var/log/ssh3-qlog && sudo chown $USER /var/log/ssh3-qlog
sudo systemctl edit ssh3-server
# thêm:
#   [Service]
#   Environment=QLOGDIR=/var/log/ssh3-qlog
sudo systemctl restart ssh3-server
```

Kéo về rồi đọc:

```bash
scp -r trungnt@<server>:/var/log/ssh3-qlog artifacts/qlog-server
python3 tools/analyze_qlog.py artifacts/qlog-server artifacts/qlogrun
```

Nhớ tắt lại (`systemctl revert ssh3-server`) trước khi chạy lấy số liệu chính.

### Đọc kết quả

`qlog_summary.csv` trả lời ba câu:

| Cột | Ý nghĩa |
|---|---|
| `cwnd_min_bytes`, `cwnd_median_bytes` | cửa sổ có sập và **ở lại** mức thấp giữa các sample không — đây là giả thuyết chính giải thích vì sao mọi phép truyền đều bị phạt, kể cả phép không mất gói |
| `lost_reordering_threshold` so với `lost_time_threshold` | mất gói được phát hiện bằng ngưỡng thứ tự (nhanh) hay ngưỡng thời gian (chậm) |
| `pto_count_max` | số lần phải chờ hết timeout thay vì phát lại nhanh |

Nếu `cwnd_median_bytes` ở mức vài KB trong khi `low` cho ra hàng trăm KB thì
giả thuyết "cửa sổ sập và không hồi" được xác nhận, và câu trả lời cho W2
không nằm ở Reno hay CUBIC mà ở cơ chế phục hồi mất gói.
