import matplotlib.pyplot as plt
import numpy as np

# Dữ liệu từ bảng Session Setup
networks = ['low', 'medium', 'high', 'VPN']

data = {
    'vim': {
        'ssh': [796.71, 1869.76, 3340.28, 1450.90],
        'ssh3': [339.42, 647.92, 914.07, 320.39],
        'mosh': [1123.81, 2355.33, 3871.06, 1631.17]
    },
    'nano': {
        'ssh': [799.31, 1837.78, 3457.45, 1188.76],
        'ssh3': [331.81, 629.46, 957.11, 293.93],
        'mosh': [1121.51, 2631.62, 3936.05, 1422.84]
    },
    'shell': {
        'ssh': [793.25, 1823.04, 3295.40, 1692.77],
        'ssh3': [327.08, 614.57, 951.07, 320.93],
        'mosh': [1144.25, 2629.97, 3965.96, 1264.13]
    }
}

fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharey=True)
fig.suptitle('Mean Session Setup Time by Protocol and Workload (ms)', fontsize=16)

x = np.arange(len(networks))
width = 0.25

colors = {'ssh': '#00FFFF', 'ssh3': '#FFD700', 'mosh': '#FFA500'}

workloads = ['vim', 'nano', 'shell']

for i, wl in enumerate(workloads):
    ax = axes[i]
    
    ax.bar(x - width, data[wl]['ssh'], width, label='ssh', color=colors['ssh'], edgecolor='black')
    ax.bar(x, data[wl]['ssh3'], width, label='ssh3', color=colors['ssh3'], edgecolor='black')
    ax.bar(x + width, data[wl]['mosh'], width, label='mosh', color=colors['mosh'], edgecolor='black')
    
    ax.set_title(f'Workload: {wl.upper()}')
    ax.set_xlabel('Network Condition')
    if i == 0:
        ax.set_ylabel('Mean Setup Time (ms)')
        ax.legend()
        
    ax.set_xticks(x)
    ax.set_xticklabels(networks)
    ax.grid(axis='y', linestyle='--', alpha=0.7)

plt.tight_layout()
plt.savefig('session_setup_chart.png', dpi=300)
print("Đã lưu biểu đồ thành công: session_setup_chart.png")
