#!/usr/bin/env python3
import os
import glob
import pandas as pd

def main():
    # Cấu hình mapping giữa thư mục và tên hiển thị trong LaTeX
    workloads = {
        'W1': 'w1',
        'W2': 'w4',       # w4 trong file hệ thống tương đương W2 trong paper
        'W3-1p': 'w3',
        'W3-5p': 'w3-5'
    }
    
    scenarios = {
        'VPN': 'default',
        'Low': 'low',
        'Medium': 'medium',
        'High': 'high'
    }
    
    protocols = ['ssh', 'ssh3', 'mosh']
    latex_proto = {'ssh': 'SSHv2', 'ssh3': 'SSH3', 'mosh': 'Mosh'}
    
    # Store kết quả
    # data[workload][scenario][protocol] = {'mean': 0, 'std': 0, 'p95': 0}
    data = {}
    
    for wl_name, wl_folder in workloads.items():
        data[wl_name] = {}
        for scen_name, scen_folder in scenarios.items():
            data[wl_name][scen_name] = {}
            
            # Quét tất cả file w*_line_log*.csv trong thư mục kịch bản
            search_pattern = os.path.join(wl_folder, '**', scen_folder, '**', '*line_log*.csv')
            csv_files = glob.glob(search_pattern, recursive=True)
            
            # Nếu không tìm thấy, thử tìm trực tiếp vì có thể cấu trúc thư mục phẳng hơn
            if not csv_files:
                search_pattern = os.path.join(wl_folder, scen_folder, '*line_log*.csv')
                csv_files = glob.glob(search_pattern)

            # Gộp data
            df_list = []
            for f in csv_files:
                try:
                    df = pd.read_csv(f)
                    if not df.empty:
                        df_list.append(df)
                except Exception:
                    pass
            
            if df_list:
                df = pd.concat(df_list, ignore_index=True)
                # Lọc các dòng success
                if 'status' in df.columns:
                    df = df[df['status'] == 'ok']
                
                for proto in protocols:
                    df_proto = df[df['protocol'] == proto]
                    if not df_proto.empty:
                        # Tính trung bình trên tất cả các lệnh trong workload
                        mean_val = df_proto['latency_ms'].mean()
                        std_val = df_proto['latency_ms'].std()
                        p95_val = df_proto['latency_ms'].quantile(0.95)
                        
                        data[wl_name][scen_name][proto] = {
                            'mean': mean_val,
                            'std': std_val if pd.notna(std_val) else 0.0,
                            'p95': p95_val if pd.notna(p95_val) else 0.0
                        }
                    else:
                        data[wl_name][scen_name][proto] = {'mean': 0, 'std': 0, 'p95': 0}
            else:
                for proto in protocols:
                    data[wl_name][scen_name][proto] = {'mean': 0, 'std': 0, 'p95': 0}

    # ==========================================
    # 1. In Bảng 1 (Mean ± Std)
    # ==========================================
    output = "\\begin{table*}[t]\n\\centering\n"
    output += "\\caption{Mean latency (ms) and standard deviation for the three workloads across protocols and network scenarios. \\textbf{Bold} marks the lowest mean value in each column.}\n"
    output += "\\label{tab:workload-latency}\n\\resizebox{\\textwidth}{!}{%\n\\begin{tabular}{l|rrrr|rrrr|rrrr|rrrr}\n\\hline\n"
    output += "\\multirow{2}{*}{\\textbf{Protocol}} & \n"
    output += "\\multicolumn{4}{c|}{\\textbf{W1 (ms)}} & \n"
    output += "\\multicolumn{4}{c|}{\\textbf{W2 (ms)}} & \n"
    output += "\\multicolumn{4}{c|}{\\textbf{W3-1p (ms)}} & \n"
    output += "\\multicolumn{4}{c}{\\textbf{W3-5p (ms)}} \\\\\n\\cline{2-17}\n"
    
    header_row = " & " + " & ".join(["\\textbf{" + s + "}" for s in scenarios.keys()] * 4) + " \\\\\n\\hline\n"
    output += header_row
    
    # Tìm min mean của mỗi cột để in đậm
    min_means = {wl: {scen: float('inf') for scen in scenarios} for wl in workloads}
    for wl in workloads:
        for scen in scenarios:
            for proto in protocols:
                m = data[wl][scen][proto]['mean']
                if m > 0 and m < min_means[wl][scen]:
                    min_means[wl][scen] = m

    for proto in protocols:
        row_str = f"\\textbf{{{latex_proto[proto]}}}"
        for wl in workloads:
            for scen in scenarios:
                m = data[wl][scen][proto]['mean']
                s = data[wl][scen][proto]['std']
                
                if m == 0:
                    cell = "N/A"
                else:
                    cell_val = f"{m:.1f}$\\pm${s:.1f}"
                    if m == min_means[wl][scen]:
                        cell = f"\\textbf{{{cell_val}}}"
                    else:
                        cell = cell_val
                
                row_str += f" & {cell}"
        row_str += " \\\\\n"
        output += row_str
        
    output += "\\hline\n\\end{tabular}%\n}\n\\end{table*}\n\n"

    # ==========================================
    # 2. In Bảng 2 (p95 Tail Latency)
    # ==========================================
    output += "\\begin{table}[t]\n\\centering\n"
    output += "\\caption{Tail latency (p95, ms) under High impairment.}\n"
    output += "\\label{tab:tail-p95}\n\\setlength{\\tabcolsep}{5pt}\n\\begin{tabular}{l|rrrr}\n\\hline\n"
    output += "\\textbf{Protocol} & \\textbf{W1} & \\textbf{W2} & \\textbf{W3-1p} & \\textbf{W3-5p} \\\\\n\\hline\n"
    
    for proto in protocols:
        row_str = f"{latex_proto[proto]}"
        for wl in workloads:
            p95 = data[wl]['High'][proto]['p95']
            if p95 == 0:
                row_str += " & N/A"
            else:
                row_str += f" & {p95:.1f}"
        row_str += " \\\\\n"
        output += row_str
        
    output += "\\hline\n\\end{tabular}\n\\end{table}\n"

    print(output)
    
    with open("generated_tables.tex", "w") as f:
        f.write(output)
    
    print("\n[+] Đã lưu output vào file generated_tables.tex")

if __name__ == "__main__":
    main()
