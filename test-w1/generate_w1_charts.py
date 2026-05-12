import matplotlib.pyplot as plt
import numpy as np

# Dữ liệu được trích xuất từ 3 ảnh (được tính bằng cách lấy trung bình cộng của 4 lệnh: ls, df -h, ps aux, grep)
networks = ['low', 'medium', 'high']

# Average Mean Latency (ms)
data_latency = {
    'ssh': [71.50, 171.79, 325.59],
    'ssh3': [72.54, 183.33, 373.02],
    'mosh': [82.00, 170.39, 286.90]
}

# Average Session Setup Latency (ms)
data_setup = {
    'ssh': [783.18, 2055.52, 3370.69],
    'ssh3': [337.78, 669.35, 1050.46],
    'mosh': [1161.61, 2582.32, 3986.32]
}

colors = {'ssh': '#00FFFF', 'ssh3': '#FFD700', 'mosh': '#FFA500'}

def plot_latency():
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.suptitle('W1 Command Loop: Mean Latency by Protocol (ms)', fontsize=16)

    x = np.arange(len(networks))

    ax.plot(x, data_latency['ssh'], marker='o', linewidth=2, markersize=8, label='ssh', color=colors['ssh'])
    ax.plot(x, data_latency['ssh3'], marker='s', linewidth=2, markersize=8, label='ssh3', color=colors['ssh3'])
    ax.plot(x, data_latency['mosh'], marker='^', linewidth=2, markersize=8, label='mosh', color=colors['mosh'])
    
    ax.set_ylabel('Mean Latency (ms)')
    ax.set_xlabel('Network Condition')
    ax.set_xticks(x)
    ax.set_xticklabels(networks)
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    ax.legend()

    plt.tight_layout()
    plt.savefig('w1_mean_latency_chart.png', dpi=300)
    print("Đã lưu biểu đồ thành công: w1_mean_latency_chart.png")

def plot_setup():
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.suptitle('W1 Command Loop: Session Setup Latency (ms)', fontsize=16)

    x = np.arange(len(networks))
    width = 0.25

    ax.bar(x - width, data_setup['ssh'], width, label='ssh', color=colors['ssh'], edgecolor='black')
    ax.bar(x, data_setup['ssh3'], width, label='ssh3', color=colors['ssh3'], edgecolor='black')
    ax.bar(x + width, data_setup['mosh'], width, label='mosh', color=colors['mosh'], edgecolor='black')
    
    ax.set_ylabel('Mean Setup Time (ms)')
    ax.set_xlabel('Network Condition')
    ax.set_xticks(x)
    ax.set_xticklabels(networks)
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    ax.legend()

    plt.tight_layout()
    plt.savefig('w1_session_setup_chart.png', dpi=300)
    print("Đã lưu biểu đồ thành công: w1_session_setup_chart.png")

if __name__ == '__main__':
    plot_latency()
    plot_setup()
