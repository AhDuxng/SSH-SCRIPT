import os
import pandas as pd
import matplotlib.pyplot as plt
import glob

# Load data from 13, 15, 20
base_dir = '/home/twan/NETWORK/COMPARE_MOSH_SSH_SSH3/w3/SSH-SCRIPT/test-w3-1pane/w3_results'

folders = ['13', '15', '20']

all_data = []
for folder in folders:
    path_pattern = os.path.join(base_dir, folder, 'w3_raw_samples.csv')
    files = glob.glob(path_pattern)
    for f in files:
        df = pd.read_csv(f)
        df['folder'] = folder
        all_data.append(df)

if all_data:
    df = pd.concat(all_data)
    
    # Calculate average execution time or response time per protocol over trials
    # Group by trial_id/timestamp and protocol
    
    plt.figure(figsize=(10, 6))
    for protocol in df['protocol'].unique():
        proto_data = df[df['protocol'] == protocol]
        plt.plot(range(len(proto_data)), proto_data['latency']*1000 if 'latency' in proto_data.columns else proto_data['duration']*1000 if 'duration' in proto_data.columns else proto_data['response_time']*1000 if 'response_time' in proto_data.columns else proto_data.iloc[:, -1], label=protocol, marker='o')

    plt.title('Comparison of Protocols across different conditions (13, 15, 20)')
    plt.ylabel('Measurements')
    plt.xlabel('Samples/Trials')
    plt.legend()
    plt.grid(True)
    out_path = os.path.join(base_dir, 'trend_chart.png')
    plt.savefig(out_path)
    print(f"Chart saved to {out_path}")
    print(df.head())
else:
    print("No data found")
