import matplotlib.pyplot as plt
import numpy as np

# Các kịch bản mạng
networks = ['low', 'medium', 'high']
workloads = ['top', 'tail', 'ping']

# --- DỮ LIỆU ĐƯỢC TRÍCH XUẤT TỪ 3 ẢNH BẠN CUNG CẤP ---

# 1. Median Latency (ms)
data_median = {
    'top': {'ssh': [84.75, 132.63, 219.83], 'ssh3': [84.80, 124.72, 215.26], 'mosh': [84.30, 50.92, 50.91]},
    'tail': {'ssh': [52.08, 52.00, 44.26], 'ssh3': [52.11, 52.01, 45.83], 'mosh': [52.15, 53.76, 0.15]},
    'ping': {'ssh': [103.86, 103.47, 100.99], 'ssh3': [103.85, 103.88, 101.76], 'mosh': [103.88, 103.83, 101.28]}
}

# 2. Std - Độ giật/Jitter (ms)
data_std = {
    'top': {'ssh': [17.09, 89.05, 192.36], 'ssh3': [13.97, 51.41, 141.53], 'mosh': [22.11, 35.64, 85.79]},
    'tail': {'ssh': [2.09, 28.25, 58.89], 'ssh3': [2.06, 22.18, 52.65], 'mosh': [27.66, 39.26, 94.75]},
    'ping': {'ssh': [1.19, 33.02, 73.70], 'ssh3': [0.27, 26.99, 55.28], 'mosh': [0.39, 29.73, 68.61]}
}

# 3. Max - Đứt gãy tắc nghẽn (ms)
data_max = {
    'top': {'ssh': [223.42, 809.95, 1494.76], 'ssh3': [210.45, 470.46, 979.88], 'mosh': [270.16, 454.90, 577.30]},
    'tail': {'ssh': [55.00, 368.31, 547.18], 'ssh3': [54.75, 235.72, 558.26], 'mosh': [310.91, 402.37, 648.68]},
    'ping': {'ssh': [136.32, 529.70, 750.07], 'ssh3': [107.91, 320.69, 717.31], 'mosh': [107.96, 416.27, 617.76]}
}

# 4. Session Setup (Median)
data_setup = {
    'top': {'ssh': [792.32, 1783.24, 3091.51], 'ssh3': [333.94, 618.01, 937.10], 'mosh': [1141.43, 2269.61, 3973.83]},
    'tail': {'ssh': [832.11, 1813.07, 3242.36], 'ssh3': [402.50, 653.55, 985.01], 'mosh': [1188.25, 2505.86, 3674.81]},
    'ping': {'ssh': [813.61, 1786.02, 3238.72], 'ssh3': [388.99, 662.87, 932.88], 'mosh': [1172.87, 2289.02, 4476.50]}
}

colors = {'ssh': '#00FFFF', 'ssh3': '#FFD700', 'mosh': '#FFA500'}

def plot_metric(metric_data, metric_name, filename, is_bar=False):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=not is_bar)
    fig.suptitle(f'W2 Continuous Monitoring: {metric_name} by Protocol and Workload (ms)', fontsize=16, fontweight='bold')

    x = np.arange(len(networks))
    width = 0.25

    for i, wl in enumerate(workloads):
        ax = axes[i]
        
        if is_bar:
            ax.bar(x - width, metric_data[wl]['ssh'], width, label='ssh', color=colors['ssh'], edgecolor='black')
            ax.bar(x, metric_data[wl]['ssh3'], width, label='ssh3', color=colors['ssh3'], edgecolor='black')
            ax.bar(x + width, metric_data[wl]['mosh'], width, label='mosh', color=colors['mosh'], edgecolor='black')
        else:
            ax.plot(x, metric_data[wl]['ssh'], marker='o', linewidth=2, markersize=8, label='ssh', color=colors['ssh'])
            ax.plot(x, metric_data[wl]['ssh3'], marker='s', linewidth=2, markersize=8, label='ssh3', color=colors['ssh3'])
            ax.plot(x, metric_data[wl]['mosh'], marker='^', linewidth=2, markersize=8, label='mosh', color=colors['mosh'])
        
        ax.set_title(f'Workload: {wl.upper()}', fontsize=14)
        ax.set_xticks(x)
        ax.set_xticklabels(networks, fontsize=12)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        
        if i == 0:
            ax.set_ylabel(f'{metric_name} (ms)', fontsize=12)
            ax.legend(fontsize=12)

    plt.tight_layout()
    plt.savefig(filename, dpi=300)
    print(f"Đã lưu biểu đồ thành công: {filename}")

if __name__ == '__main__':
    plot_metric(data_median, 'Median Latency', 'w2_median_latency_chart.png', is_bar=False)
    plot_metric(data_std, 'Std (Jitter)', 'w2_std_jitter_chart.png', is_bar=False)
    plot_metric(data_max, 'Max Latency', 'w2_max_latency_chart.png', is_bar=False)
    plot_metric(data_setup, 'Session Setup Latency', 'w2_session_setup_chart.png', is_bar=True)
