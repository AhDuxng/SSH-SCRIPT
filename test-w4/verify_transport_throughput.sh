#!/usr/bin/env bash
# verify_transport_throughput.sh — So sánh throughput TCP (iperf3) vs SSH vs SSH3
# trên cùng link netem để xác minh xem SSH3 chậm do transport (quic-go) hay do cách đo.
#
# Quy trình:
#   1. Áp tc netem (nếu chưa áp) theo scenario
#   2. Chạy iperf3 TCP (baseline throughput)
#   3. Truyền file 10 MiB qua SSH (scp) và đo thời gian
#   4. Truyền file 10 MiB qua SSH3 và đo thời gian
#   5. So sánh throughput
#
# Usage:
#   ./verify_transport_throughput.sh medium    # hoặc low / high
#   ./verify_transport_throughput.sh medium --skip-netem  # nếu tc đã áp sẵn
#
# Yêu cầu trên Pi:
#   - iperf3 (sudo apt install iperf3)
#   - File /tmp/w4_paths_large.txt đã tồn tại (chạy setup_w4_fixtures.sh trước)
#
set -euo pipefail

SCENARIO="${1:?Usage: $0 <scenario: low|medium|high> [--skip-netem]}"
SKIP_NETEM="${2:-}"

HOST="10.42.0.206"
USER_NAME="pi"
SOURCE_IP="10.42.0.1"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

CLIENT_IFACE="${CLIENT_IFACE:-enp43s0}"
SERVER_IFACE="${SERVER_IFACE:-eth0}"
LOCAL_SET_NETWORK="${LOCAL_SET_NETWORK:-../set_network.sh}"
REMOTE_SET_NETWORK="${REMOTE_SET_NETWORK:-~/set_network.sh}"

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -i $IDENTITY_FILE"
SSH3_PATH=":4433/ssh3-term"

IPERF_DURATION=10
TRANSFER_FILE="/tmp/w4_paths_large.txt"  # 10 MiB trên Pi
LOCAL_TMP="/tmp/w4_verify_transfer_$$"

RESULT_DIR="w4_results/_verify_transport"
mkdir -p "$RESULT_DIR"
RESULT_FILE="$RESULT_DIR/throughput_${SCENARIO}_$(date +%Y%m%d_%H%M%S).txt"

log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }

cleanup() {
    rm -f "$LOCAL_TMP" 2>/dev/null || true
    # Kill iperf3 server on Pi if still running
    ssh $SSH_OPTS "$USER_NAME@$HOST" "pkill -f 'iperf3 -s' 2>/dev/null || true" 2>/dev/null || true
}
trap cleanup EXIT

# --- Áp tc netem nếu cần ---
if [[ "$SKIP_NETEM" != "--skip-netem" ]]; then
    log "Áp tc netem scenario=$SCENARIO trên cả client và server..."
    sudo bash "$LOCAL_SET_NETWORK" "$CLIENT_IFACE" "$SCENARIO"
    ssh -t $SSH_OPTS "$USER_NAME@$HOST" \
        "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH bash $REMOTE_SET_NETWORK $SERVER_IFACE $SCENARIO"
    log "Chờ 10s cho link ổn định..."
    sleep 10
fi

# --- Verify ping RTT ---
log "=== Ping RTT verification ==="
ping -c 5 -i 0.5 "$HOST" 2>&1 | tail -2 | tee -a "$RESULT_FILE"
echo | tee -a "$RESULT_FILE"

# --- Test 1: iperf3 TCP ---
log "=== Test 1: iperf3 TCP (${IPERF_DURATION}s) ==="
log "Khởi động iperf3 server trên Pi..."
ssh $SSH_OPTS "$USER_NAME@$HOST" "pkill -f 'iperf3 -s' 2>/dev/null; sleep 0.5; iperf3 -s -D"
sleep 2

log "Chạy iperf3 client (server→client, tức Pi gửi data)..."
echo "--- iperf3 TCP (server→client) ---" | tee -a "$RESULT_FILE"
iperf3 -c "$HOST" -R -t "$IPERF_DURATION" -b "$SOURCE_IP" 2>&1 | tee -a "$RESULT_FILE" | grep -E "sender|receiver"
echo | tee -a "$RESULT_FILE"

log "Chạy iperf3 client (client→server, tức client gửi data)..."
echo "--- iperf3 TCP (client→server) ---" | tee -a "$RESULT_FILE"
iperf3 -c "$HOST" -t "$IPERF_DURATION" -b "$SOURCE_IP" 2>&1 | tee -a "$RESULT_FILE" | grep -E "sender|receiver"
echo | tee -a "$RESULT_FILE"

ssh $SSH_OPTS "$USER_NAME@$HOST" "pkill -f 'iperf3 -s' 2>/dev/null || true"

# --- Test 2: SSH file transfer (scp) ---
log "=== Test 2: SSH transfer (10 MiB file via scp) ==="
echo "--- SSH scp transfer (Pi→client, 10 MiB) ---" | tee -a "$RESULT_FILE"

SSH_TIMES=()
for i in $(seq 1 3); do
    rm -f "$LOCAL_TMP"
    start_ms=$(date +%s%N)
    scp $SSH_OPTS "$USER_NAME@$HOST:$TRANSFER_FILE" "$LOCAL_TMP" 2>/dev/null
    end_ms=$(date +%s%N)
    elapsed_ms=$(( (end_ms - start_ms) / 1000000 ))
    file_size=$(wc -c < "$LOCAL_TMP")
    throughput_kib=$(echo "scale=2; $file_size / 1024 / ($elapsed_ms / 1000)" | bc)
    throughput_mbit=$(echo "scale=2; $file_size * 8 / 1000000 / ($elapsed_ms / 1000)" | bc)
    SSH_TIMES+=("$elapsed_ms")
    echo "  Run $i: ${elapsed_ms}ms, ${throughput_kib} KiB/s (${throughput_mbit} Mbit/s)" | tee -a "$RESULT_FILE"
done
echo | tee -a "$RESULT_FILE"

# --- Test 3: SSH cat qua pipe (giống cách W4 đo) ---
log "=== Test 3: SSH cat pipe (giống W4 measurement) ==="
echo "--- SSH cat pipe (Pi→client, 10 MiB) ---" | tee -a "$RESULT_FILE"

for i in $(seq 1 3); do
    rm -f "$LOCAL_TMP"
    start_ms=$(date +%s%N)
    ssh $SSH_OPTS "$USER_NAME@$HOST" "cat $TRANSFER_FILE" > "$LOCAL_TMP" 2>/dev/null
    end_ms=$(date +%s%N)
    elapsed_ms=$(( (end_ms - start_ms) / 1000000 ))
    file_size=$(wc -c < "$LOCAL_TMP")
    throughput_kib=$(echo "scale=2; $file_size / 1024 / ($elapsed_ms / 1000)" | bc)
    throughput_mbit=$(echo "scale=2; $file_size * 8 / 1000000 / ($elapsed_ms / 1000)" | bc)
    echo "  Run $i: ${elapsed_ms}ms, ${throughput_kib} KiB/s (${throughput_mbit} Mbit/s)" | tee -a "$RESULT_FILE"
done
echo | tee -a "$RESULT_FILE"

# --- Test 4: SSH3 cat qua pipe ---
log "=== Test 4: SSH3 cat pipe (giống W4 measurement) ==="
echo "--- SSH3 cat pipe (Pi→client, 10 MiB) ---" | tee -a "$RESULT_FILE"

for i in $(seq 1 3); do
    rm -f "$LOCAL_TMP"
    start_ms=$(date +%s%N)
    # SSH3 không hỗ trợ pipe trực tiếp như ssh, nên dùng interactive mode
    # Gửi lệnh cat và capture output
    ssh3 -privkey "$IDENTITY_FILE" -insecure "$USER_NAME@$HOST$SSH3_PATH" \
        -command "cat $TRANSFER_FILE" > "$LOCAL_TMP" 2>/dev/null || true
    end_ms=$(date +%s%N)
    elapsed_ms=$(( (end_ms - start_ms) / 1000000 ))
    file_size=$(wc -c < "$LOCAL_TMP" 2>/dev/null || echo 0)
    if [[ "$file_size" -gt 0 ]]; then
        throughput_kib=$(echo "scale=2; $file_size / 1024 / ($elapsed_ms / 1000)" | bc)
        throughput_mbit=$(echo "scale=2; $file_size * 8 / 1000000 / ($elapsed_ms / 1000)" | bc)
        echo "  Run $i: ${elapsed_ms}ms, ${throughput_kib} KiB/s (${throughput_mbit} Mbit/s), received=${file_size} bytes" | tee -a "$RESULT_FILE"
    else
        echo "  Run $i: ${elapsed_ms}ms, SSH3 pipe failed (0 bytes received)" | tee -a "$RESULT_FILE"
    fi
done
echo | tee -a "$RESULT_FILE"

# --- Test 5: SSH3 interactive (giống chính xác cách W4 đo) ---
log "=== Test 5: SSH3 interactive timed (mô phỏng W4) ==="
echo "--- SSH3 interactive: time cat 10MiB inside session ---" | tee -a "$RESULT_FILE"
echo "  (Dùng Python snippet để đo chính xác như W4)" | tee -a "$RESULT_FILE"

python3 -c "
import pexpect, time, sys

HOST = '$HOST'
USER = '$USER_NAME'
KEY = '$IDENTITY_FILE'
SSH3_PATH = '$SSH3_PATH'
FILE = '$TRANSFER_FILE'

cmd = f'ssh3 -privkey {KEY} -insecure {USER}@{HOST}{SSH3_PATH}'
print(f'  Spawning: {cmd}')

child = pexpect.spawn(cmd, encoding='utf-8', codec_errors='ignore', timeout=300, maxread=65536)
child.expect(r'[#\$>]\s*$', timeout=60)
child.sendline('export PS1=\"TEST# \"')
child.expect('TEST# ', timeout=10)

results = []
for i in range(3):
    child.sendline(f'{{ cat {FILE}; }} 2>&1; echo __DONE_{i}__')
    start = time.perf_counter()
    child.expect(f'__DONE_{i}__', timeout=300)
    elapsed_ms = (time.perf_counter() - start) * 1000
    results.append(elapsed_ms)
    print(f'  Run {i+1}: {elapsed_ms:.1f} ms ({10240 / (elapsed_ms/1000):.1f} KiB/s)')
    time.sleep(1)

child.sendline('exit')
child.close()

avg = sum(results) / len(results)
print(f'  Average: {avg:.1f} ms ({10240 / (avg/1000):.1f} KiB/s)')
" 2>&1 | tee -a "$RESULT_FILE"
echo | tee -a "$RESULT_FILE"

# --- Test 6: SSH interactive (đối chứng) ---
log "=== Test 6: SSH interactive timed (đối chứng) ==="
echo "--- SSH interactive: time cat 10MiB inside session ---" | tee -a "$RESULT_FILE"

python3 -c "
import pexpect, time, sys

HOST = '$HOST'
USER = '$USER_NAME'
KEY = '$IDENTITY_FILE'
FILE = '$TRANSFER_FILE'

cmd = f'ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i {KEY} {USER}@{HOST}'
print(f'  Spawning: {cmd}')

child = pexpect.spawn(cmd, encoding='utf-8', codec_errors='ignore', timeout=300, maxread=65536)
child.expect(r'[#\$>]\s*$', timeout=60)
child.sendline('export PS1=\"TEST# \"')
child.expect('TEST# ', timeout=10)

results = []
for i in range(3):
    child.sendline(f'{{ cat {FILE}; }} 2>&1; echo __DONE_{i}__')
    start = time.perf_counter()
    child.expect(f'__DONE_{i}__', timeout=300)
    elapsed_ms = (time.perf_counter() - start) * 1000
    results.append(elapsed_ms)
    print(f'  Run {i+1}: {elapsed_ms:.1f} ms ({10240 / (elapsed_ms/1000):.1f} KiB/s)')
    time.sleep(1)

child.sendline('exit')
child.close()

avg = sum(results) / len(results)
print(f'  Average: {avg:.1f} ms ({10240 / (avg/1000):.1f} KiB/s)')
" 2>&1 | tee -a "$RESULT_FILE"
echo | tee -a "$RESULT_FILE"

# --- Summary ---
log "=== SUMMARY ==="
echo "============================================" | tee -a "$RESULT_FILE"
echo "Scenario: $SCENARIO" | tee -a "$RESULT_FILE"
echo "Kết quả đầy đủ: $RESULT_FILE" | tee -a "$RESULT_FILE"
echo "============================================" | tee -a "$RESULT_FILE"
echo | tee -a "$RESULT_FILE"
echo "Cách đọc kết quả:" | tee -a "$RESULT_FILE"
echo "  - Nếu iperf3 TCP >> SSH throughput → SSH bị bottleneck ở encryption/PTY" | tee -a "$RESULT_FILE"
echo "  - Nếu SSH throughput >> SSH3 throughput → quic-go transport là bottleneck" | tee -a "$RESULT_FILE"
echo "  - Nếu SSH interactive ≈ SSH pipe → cách đo W4 (pexpect) không gây bias" | tee -a "$RESULT_FILE"
echo "  - Nếu SSH3 interactive ≈ SSH3 pipe → vấn đề nằm ở transport, không phải pexpect" | tee -a "$RESULT_FILE"

log "Done. Results saved to: $RESULT_FILE"
