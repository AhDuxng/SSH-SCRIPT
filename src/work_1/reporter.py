def print_report(protocols, commands, build_summary_row):
    print("\n" + "=" * 118)
    print(
        f"{'Protocol':<8} | {'Command':<20} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
        f"{'Mean(ms)':>9} | {'Median':>9} | {'StdDev':>9} | {'P95':>9} | {'CI95+/-':>9}"
    )
    print("-" * 118)
    for protocol in protocols:
        for command in commands:
            summary = build_summary_row(protocol, command)
            mean_text = f"{summary['mean_ms']:.2f}" if summary["mean_ms"] is not None else "N/A"
            median_text = f"{summary['median_ms']:.2f}" if summary["median_ms"] is not None else "N/A"
            stdev_text = f"{summary['stdev_ms']:.2f}" if summary["stdev_ms"] is not None else "N/A"
            p95_text = f"{summary['p95_ms']:.2f}" if summary["p95_ms"] is not None else "N/A"
            ci95_text = f"{summary['ci95_half_width_ms']:.2f}" if summary["ci95_half_width_ms"] is not None else "N/A"

            print(
                f"{protocol:<8} | {command:<20} | {summary['n']:>4} | {summary['failures']:>4} | {summary['success_rate_pct']:>8.1f} | "
                f"{mean_text:>9} | {median_text:>9} | {stdev_text:>9} | {p95_text:>9} | {ci95_text:>9}"
            )
        print("-" * 118)
