import matplotlib.pyplot as plt
import numpy as np

# Dữ liệu từ bảng
networks = ['low', 'medium', 'high', 'VPN']

data = {
    'vim': {
        'ssh': [101.55, 185.56, 374.02, 155.53],
        'ssh3': [101.71, 177.44, 321.98, 136.12],
        'mosh': [862.53, 1258.83, 911.16, 1374.47]
    },
    'nano': {
        'ssh': [101.57, 186.24, 373.57, 153.68],
        'ssh3': [101.74, 175.95, 326.79, 136.71],
        'mosh': [895.28, 1231.87, 874.24, 1234.35]
    },
    'shell': {
        'ssh': [101.82, 219.61, 452.67, 149.62],
        'ssh3': [101.90, 190.22, 361.73, 138.11],
        'mosh': [136.16, 169.50, 253.07, 112.03]
    }
}

fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=False)
fig.suptitle('Mean Keystroke Latency by Protocol and Workload (ms)', fontsize=16)

x = np.arange(len(networks))
width = 0.25

colors = {'ssh': '#00FFFF', 'ssh3': '#FFD700', 'mosh': '#FFA500'} # Tương tự màu trong bảng

workloads = ['vim', 'nano', 'shell']

for i, wl in enumerate(workloads):
    ax = axes[i]
    
    ax.plot(x, data[wl]['ssh'], marker='o', linewidth=2, markersize=8, label='ssh', color=colors['ssh'])
    ax.plot(x, data[wl]['ssh3'], marker='s', linewidth=2, markersize=8, label='ssh3', color=colors['ssh3'])
    ax.plot(x, data[wl]['mosh'], marker='^', linewidth=2, markersize=8, label='mosh', color=colors['mosh'])
    
    ax.set_title(f'Workload: {wl.upper()}')
    ax.set_ylabel('Mean Latency (ms)')
    ax.set_xticks(x)
    ax.set_xticklabels(networks)
    ax.grid(axis='y', linestyle='--', alpha=0.7)
    if i == 0:
        ax.legend()

plt.tight_layout()
plt.savefig('mean_latency_chart.png', dpi=300)
print("Đã lưu biểu đồ thành công: mean_latency_chart.png")
