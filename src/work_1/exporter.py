import csv
import json


def export_results(
    json_path,
    csv_path,
    started_at,
    target,
    iterations,
    warmup_rounds,
    timeout_sec,
    random_seed,
    protocols,
    commands,
    results,
    failures,
    build_summary_row,
):
    payload = {
        "meta": {
            "started_at_utc": started_at,
            "target": target,
            "iterations": iterations,
            "warmup_rounds": warmup_rounds,
            "timeout_sec": timeout_sec,
            "random_seed": random_seed,
            "protocols": protocols,
            "commands": commands,
        },
        "summary": [],
        "raw_samples_ms": results,
        "failures": failures,
    }

    for protocol in protocols:
        for command in commands:
            payload["summary"].append(build_summary_row(protocol, command))

    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(payload, json_file, indent=2)

    with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["protocol", "command", "sample_index", "latency_ms"])
        for protocol in protocols:
            for command in commands:
                for index, value in enumerate(results[protocol][command], start=1):
                    writer.writerow([protocol, command, index, f"{value:.6f}"])

    print(f"\nSaved JSON report: {json_path}")
    print(f"Saved raw CSV: {csv_path}")
