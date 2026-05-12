import matplotlib.pyplot as plt
import numpy as np

# Các kịch bản mạng và lệnh
networks = ['low', 'medium', 'high', 'VPN']
commands = ['ls', 'df -h', 'ps aux', 'grep']

# Dữ liệu từ bảng tổng hợp bạn vừa cung cấp (Mean)
data = {
    'ls': {
        'ssh': [71.36, 164.39, 288.99, 58.41],
        'ssh3': [72.60, 158.10, 275.05, 54.74],
        'mosh': [82.00, 171.75, 280.03, 63.98]
    },
    'df -h': {
        'ssh': [71.32, 168.55, 296.97, 58.62],
        'ssh3': [72.62, 156.42, 274.79, 55.01],
        'mosh': [81.95, 173.53, 287.09, 55.01]
    },
    'ps aux': {
        'ssh': [71.89, 188.18, 425.55, 85.01],
        'ssh3': [72.20, 261.90, 676.28, 54.47],
        'mosh': [81.70, 164.47, 276.23, 68.21]
    },
    'grep': {
        'ssh': [71.44, 166.06, 290.87, 57.79],
        'ssh3': [72.73, 156.90, 265.98, 55.07],
        'mosh': [82.37, 171.81, 304.27, 64.04]
    }
}

colors = {'ssh': '#00FFFF', 'ssh3': '#FFD700', 'mosh': '#FFA500'}

def plot_combined_chart():
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('W1 Command Loop: Mean Completion Latency (ms)', fontsize=18, fontweight='bold')

    x = np.arange(len(networks))

    for i, cmd in enumerate(commands):
        ax = axes[i // 2, i % 2]
        
        ax.plot(x, data[cmd]['ssh'], marker='o', linewidth=2, markersize=8, label='ssh', color=colors['ssh'])
        ax.plot(x, data[cmd]['ssh3'], marker='s', linewidth=2, markersize=8, label='ssh3', color=colors['ssh3'])
        ax.plot(x, data[cmd]['mosh'], marker='^', linewidth=2, markersize=8, label='mosh', color=colors['mosh'])

        ax.set_title(f'Command: {cmd}', fontsize=14, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(networks, fontsize=12)
        ax.set_ylabel('Mean Latency (ms)', fontsize=12)
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        if i == 0:
            ax.legend(fontsize=12)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig('w1_mean_command_latency_chart_final.png', dpi=300)
    print("Đã lưu biểu đồ thành công: w1_mean_command_latency_chart_final.png")

if __name__ == '__main__':
    plot_combined_chart()
