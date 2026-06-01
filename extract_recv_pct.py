#!/usr/bin/env python3
import json
import csv
import glob
from pathlib import Path

def main():
    print(f"={' RECV_PCT REPORT ':=^105}")
    print(f"{'Test':<5} | {'Scenario':<10} | {'Protocol':<8} | {'Command':<45} | {'Recv% Mean':>10} | {'Recv% Min':>10}")
    print("-" * 105)

    csv_data = []
    
    # Tìm tất cả các file meta.json trong các thư mục kết quả (của cả w1 và w4)
    search_pattern = "test-*/w*_results*/**/w*_meta.json"
    files = sorted(glob.glob(search_pattern, recursive=True))

    for meta_file in files:
        with open(meta_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue

            scenario = data.get("scenario", "default")
            test_type = "W1" if "w1_" in meta_file else "W4"
            
            for row in data.get("summary", []):
                proto = row.get("protocol", "")
                cmd = row.get("command", "")
                mean = row.get("recv_pct_mean")
                mini = row.get("recv_pct_min")
                
                mean_str = f"{mean:.2f}%" if mean is not None else "N/A"
                min_str = f"{mini:.2f}%" if mini is not None else "N/A"
                
                print(f"{test_type:<5} | {scenario:<10} | {proto:<8} | {cmd:<45} | {mean_str:>10} | {min_str:>10}")
                
                csv_data.append([test_type, scenario, proto, cmd, mean, mini])

    print("=" * 105)

    # Xuất ra file CSV tổng hợp
    out_csv = "recv_pct_summary_all.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Test", "Scenario", "Protocol", "Command", "Recv_Pct_Mean", "Recv_Pct_Min"])
        writer.writerows(csv_data)
    
    print(f"\nĐã xuất báo cáo chi tiết ra file: {out_csv}")

if __name__ == "__main__":
    main()
