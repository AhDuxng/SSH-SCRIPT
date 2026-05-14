#!/usr/bin/env python3
import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

base_dir = '/home/twan/NETWORK/COMPARE_MOSH_SSH_SSH3/w3/SSH-SCRIPT/test-w3-1pane/w3_results'
scenarios = ['13', '15', '20']

def parse_data(scenario):
    data = []
    # Search for line_log.csv inside the scenario folder
    pattern = os.path.join(base_dir, scenario, '*', 'w3_line_log.csv')
    for file_path in glob.glob(pattern):
        # Extract timestamp from the folder name
        parent_dir = os.path.basename(os.path.dirname(file_path))
        try:
            timestamp = datetime.strptime(parent_dir, '%Y%m%d_%H%M%S')
        except ValueError:
            continue
            
        try:
            df = pd.read_csv(file_path)
            # Filter ok status
            df = df[df['status'].str.lower() == 'ok']
            df['latency_ms'] = pd.to_numeric(df['latency_ms'], errors='coerce')
            
            # Calculate mean latency per protocol
            mean_latencies = df.groupby('protocol')['latency_ms'].mean().to_dict()
            for proto, lat in mean_latencies.items():
                data.append({
                    'timestamp': timestamp,
                    'protocol': proto.lower(),
                    'latency_ms': lat
                })
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            
    if not data:
        return pd.DataFrame()
        
    df_res = pd.DataFrame(data)
    df_res.sort_values('timestamp', inplace=True)
    return df_res

fig, axes = plt.subplots(3, 1, figsize=(12, 15), sharey=False)
fig.suptitle('W3 1-Pane: Average Keystroke Latency Over Time', fontsize=16)

colors = {'ssh': '#1f77b4', 'mosh': '#ff7f0e', 'ssh3': '#2ca02c'}

for i, scenario in enumerate(scenarios):
    df = parse_data(scenario)
    ax = axes[i]
    if df.empty:
        ax.set_title(f'Scenario {scenario} - No Data')
        continue
        
    ax.set_title(f'Scenario {scenario}')
    ax.set_ylabel('Mean Latency (ms)')
    
    for proto in ['ssh', 'mosh', 'ssh3']:
        proto_df = df[df['protocol'] == proto]
        if not proto_df.empty:
            ax.plot(proto_df['timestamp'], proto_df['latency_ms'], marker='o', label=proto.upper(), color=colors.get(proto, 'k'))
            
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M\n%m-%d'))
    ax.grid(True, linestyle='--', alpha=0.7)
    ax.legend(loc='best')

plt.xlabel('Time')
plt.tight_layout(rect=[0, 0.03, 1, 0.95])

out_path = os.path.join(base_dir, 'w3_latency_over_time.png')
plt.savefig(out_path, dpi=300)
print(f"Time-series chart successfully saved to {out_path}")
