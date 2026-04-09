from benchmarker import LatencyBenchmarker
from config import TARGET_HOST, TARGET_USER


def main():
    bench = LatencyBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()
    bench.export_results()


if __name__ == "__main__":
    main()
