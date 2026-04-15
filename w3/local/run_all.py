#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description="Chạy tuần tự w3_measure.py cho nhiều giao thức")
    parser.add_argument("--host", required=True, help="IP của server")
    parser.add_argument("--user", required=True, help="Username trên remote")
    parser.add_argument("--remote-setup", default="/home/{user}/w3_tmux_setup.sh",
                        help="Đường dẫn đến w3_tmux_setup.sh trên remote")
    
    # Các thông số đo chuẩn
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--samples-per-trial", type=int, default=100)
    parser.add_argument("--warmup-samples", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--echo-timeout", type=int, default=45)
    
    parser.add_argument("--metrics", nargs="+", default=["keystroke_latency", "line_echo"],  
                        choices=["keystroke_latency", "line_echo"])
    parser.add_argument("--protocols", nargs="+", default=["ssh", "mosh", "ssh3"], 
                        choices=["ssh", "mosh", "ssh3"])
    
    parser.add_argument("--scenario", default="default", help="Kịch bản giả lập mạng (tuỳ chọn)")
    parser.add_argument("--output-dir", default="results", help="Thư mục xuất CSV")
    
    args = parser.parse_args()

    remote_setup = args.remote_setup.format(user=args.user)
    os.makedirs(args.output_dir, exist_ok=True)

    # Khởi tạo tham số dòng lệnh cốt lõi
    commands = {
        "ssh": f"ssh -tt -o StrictHostKeyChecking=no -o ControlPath=none {args.user}@{args.host}",
        "mosh": f"mosh --predict never {args.user}@{args.host}",
        "ssh3": f"ssh3 -insecure {args.user}@{args.host}/ssh3-term"
    }

    # Tìm đường dẫn tuyệt đối cho w3_measure và w3_stats để script chạy chéo thư mục không bị lỗi
    base_dir = os.path.dirname(os.path.abspath(__file__))
    measure_script = os.path.join(base_dir, "w3_measure.py")
    stats_script = os.path.join(base_dir, "w3_stats.py")

    results = []

    for metric in args.metrics:
        for proto in args.protocols:
            cmd_str = commands.get(proto)
            if not cmd_str:
                print(f"[!] Bỏ qua giao thức không hợp lệ: {proto}")
                continue
                
            out_csv = os.path.join(args.output_dir, f"w3_{proto}_{metric}.csv")
            
            print(f"\n{'='*60}")
            print(f"▶ ĐANG ĐO: Protocol = {proto.upper()} | Metric = {metric}")
            print(f"{'='*60}")
            
            run_cmd = [
                sys.executable, measure_script,
                "--cmd", cmd_str,
                "--remote-setup", remote_setup,
                "--trials", str(args.trials),
                "--samples-per-trial", str(args.samples_per_trial),
                "--warmup-samples", str(args.warmup_samples),
                "--timeout", str(args.timeout),
                "--echo-timeout", str(args.echo_timeout),
                "--metric", metric,
                "--proto", proto,
                "--scenario", args.scenario,
                "--output", out_csv
            ]
            
            try:
                subprocess.run(run_cmd, check=True)
                results.append(out_csv)
            except subprocess.CalledProcessError as e:
                print(f"[!] Lỗi khi chạy {proto}: {e}")
            except KeyboardInterrupt:
                print("\n[!] Đã bị ngắt bởi người dùng.")
                sys.exit(1)

    print(f"\n{'='*60}")
    print("▶ TỔNG HỢP KẾT QUẢ THỐNG KÊ (Bỏ qua Warm-up, khớp 100% logic src/w3)")
    print(f"{'='*60}")
    for res in results:
        if os.path.exists(res):
            print(f"\n--- {os.path.basename(res)} ---")
            subprocess.run([sys.executable, stats_script, res])

if __name__ == "__main__":
    main()
