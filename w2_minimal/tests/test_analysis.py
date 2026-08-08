import re
import subprocess
import sys
import unittest
from pathlib import Path

import pexpect

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from analyze_w2 import summarize_load, summarize_samples
from clock_sync import estimate_clock_offset
from continuous_workloads import (
    SEQUENCE, TIMESTAMP_19, collect_events, build_output_load_command, build_preflight_command,
)
from run_w2 import build_schedule
from terminal_io import ECHO_GAP, clean_digits, gapped_literal


def make_sample(protocol, workload, index, status, latency=""):
    return {
        "trial_id": f"{protocol}_{workload}_r01",
        "protocol": protocol,
        "workload": workload,
        "sample_index": str(index),
        "status": status,
        "latency_ms": str(latency),
    }


class FakeChild:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0
        self.read_sizes = []

    def read_nonblocking(self, size, timeout):
        self.read_sizes.append(size)
        if self._index >= len(self._chunks):
            raise pexpect.EOF("end of fake data")
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class FakeRunner:
    def expect_prompt(self, _child, _timeout=None):
        pass


class AnalysisTests(unittest.TestCase):
    def test_schedule_uses_complete_blocks(self):
        rows = build_schedule(
            ["ssh", "ssh3"],
            ["find_usr", "docker_logs", "large_file"],
            2, 42, "test", "low",
        )
        self.assertEqual(len(rows), 12)
        first = {(row["protocol"], row["workload"]) for row in rows[:6]}
        self.assertEqual(len(first), 6)

    def test_summary_reports_event_latency_and_completion(self):
        rows = [
            make_sample("ssh", "large_file", 1, "success", 100),
            make_sample("ssh", "large_file", 2, "success", 200),
            make_sample("ssh", "large_file", 3, "timeout"),
        ]
        summary = summarize_samples(rows)[0]
        self.assertEqual(summary["mean_ms"], "150.000000")
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["success_rate_pct"], "66.667")

    def test_summary_excludes_clock_invalid_from_latency(self):
        rows = [
            make_sample("ssh", "large_file", 1, "success", 100),
            make_sample("ssh", "large_file", 2, "success", 200),
            make_sample("ssh", "large_file", 3, "clock_invalid", "-5.123456"),
            make_sample("ssh", "large_file", 4, "timeout"),
        ]
        summary = summarize_samples(rows)[0]
        self.assertEqual(summary["mean_ms"], "150.000000")
        self.assertEqual(summary["samples"], 4)
        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["success_rate_pct"], "50.000")
        self.assertEqual(summary["clock_invalid_count"], 1)

    def test_load_summary_reports_observed_rate(self):
        rows = [{
            "trial_id": "ssh_find_usr_r01",
            "protocol": "ssh",
            "workload": "find_usr",
            "status": "success",
            "observed_rate_bytes_per_sec": "1040000",
            "configured_rate_bytes_per_sec": "1048576",
        }]
        summary = summarize_load(rows)[0]
        self.assertEqual(summary["measured_connections"], 1)
        self.assertEqual(summary["observed_rate_mean_bytes_per_sec"], "1040000.000000")
        self.assertAlmostEqual(float(summary["mean_configured_rate_pct"]), 99.182129, places=5)

    def test_gapped_event_marker(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        text = "W2_OUTPUT_EVENT_12:\x1b[31m1785988222614578891"
        match = pattern.search(text)
        self.assertIsNotNone(match)
        self.assertEqual(clean_digits(match.group(1)), "12")
        self.assertEqual(clean_digits(match.group(2)), "1785988222614578891")

    def test_clean_digits(self):
        self.assertEqual(clean_digits("1\x1b[31m2\r\n3"), "123")

    def test_collect_events_split_marker(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        chunks = [
            "W2_OUTPUT_EVENT_",
            "12:\x1b[31m1785988222614578891",
            "\n",
        ]
        child = FakeChild(chunks)
        results = []
        def cb(idx, seq, latency, remote, recv):
            results.append((idx, seq, latency, remote, recv))
            return True
        cfg = {
            "WARMUP_SAMPLES": "0",
            "SAMPLES_PER_TRIAL": "1",
            "EVENT_TIMEOUT": "1",
            "_CLOCK_OFFSET_NS": "0",
            "MAX_VALID_LATENCY_MS": "60000",
            "MIN_VALID_LATENCY_MS": "0",
            "EVENT_PARSE_BUFFER_CHARS": "131072",
            "EVENT_READ_BYTES": "2048",
            "EVENT_TOTAL_TIMEOUT": "5",
        }
        measurement = collect_events(child, pattern, cfg, cb)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], 12)
        self.assertEqual(child.read_sizes, [2048, 2048])
        self.assertGreater(measurement["received_bytes"], 0)
        self.assertGreater(measurement["observed_rate_bytes_per_sec"], 0)

    def test_collect_events_ansi_between_markers(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        chunks = [
            "\x1b[2J\x1b[H",
            "W2_OUTPUT_EVENT_\x1b[1m7:\x1b[0m\x1b[32m1234567890123456789\n",
            "\x1b[K",
        ]
        child = FakeChild(chunks)
        results = []
        def cb(idx, seq, latency, remote, recv):
            results.append((idx, seq, latency, remote, recv))
            return True
        cfg = {
            "WARMUP_SAMPLES": "0",
            "SAMPLES_PER_TRIAL": "1",
            "EVENT_TIMEOUT": "1",
            "_CLOCK_OFFSET_NS": "0",
            "MAX_VALID_LATENCY_MS": "60000",
            "MIN_VALID_LATENCY_MS": "0",
            "EVENT_PARSE_BUFFER_CHARS": "131072",
            "EVENT_TOTAL_TIMEOUT": "5",
        }
        collect_events(child, pattern, cfg, cb)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1], 7)

    def test_negative_latency_is_clock_invalid_not_clamped(self):
        trial = {"trial_id": "test", "protocol": "ssh", "workload": "large_file"}
        rows = []
        sample_count = 10

        def record(index, sequence, latency_ms, remote_ns, recv_ns):
            maximum = 60000.0
            minimum = 0.0
            if not minimum <= latency_ms <= maximum:
                rows.append({
                    "trial_id": "test", "protocol": "ssh", "workload": "large_file",
                    "sample_index": str(index), "remote_sequence": str(sequence),
                    "status": "clock_invalid",
                    "latency_ms": f"{latency_ms:.6f}",
                    "remote_event_ns": str(remote_ns),
                    "recv_local_ns": str(recv_ns),
                    "note": "",
                })
                return False
            rows.append({
                "trial_id": "test", "protocol": "ssh", "workload": "large_file",
                "sample_index": str(index), "remote_sequence": str(sequence),
                "status": "success",
                "latency_ms": f"{latency_ms:.6f}",
                "remote_event_ns": str(remote_ns),
                "recv_local_ns": str(recv_ns),
                "note": "",
            })
            return True

        self.assertFalse(record(1, "1", -5.0, 1000, 2000))
        self.assertEqual(rows[0]["status"], "clock_invalid")
        self.assertEqual(float(rows[0]["latency_ms"]), -5.0)

        self.assertTrue(record(2, "2", 10.0, 1000, 2000))
        self.assertEqual(rows[1]["status"], "success")
        self.assertEqual(float(rows[1]["latency_ms"]), 10.0)

    def test_rate_limiter_script_syntax(self):
        script = PROJECT_DIR / "scripts" / "rate_limit_stream.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(script)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_output_load_command_with_rate_limiter(self):
        cmd = build_output_load_command(
            "find /usr", "W2_OUTPUT_EVENT_", 0.1,
            "/tmp/w2_rate_limit_stream.py", 123456, 2048,
        )
        self.assertIn("python3 /tmp/w2_rate_limit_stream.py", cmd)
        self.assertIn("--rate 123456", cmd)
        self.assertIn("--chunk 2048", cmd)
        self.assertIn("--interval 0.1", cmd)
        self.assertIn("find /usr", cmd)
        result = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=f"{cmd}\n{result.stderr}")

    def test_output_load_command_without_rate_limiter(self):
        cmd = build_output_load_command("find /usr", "W2_OUTPUT_EVENT_", 0.1)
        self.assertIn("W2_OUTPUT_EVENT_", cmd)
        self.assertIn("find /usr", cmd)
        result = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=f"{cmd}\n{result.stderr}")

    def test_generated_shell_commands_are_valid(self):
        class FakeChild:
            def __init__(self):
                self.commands = []

            def sendline(self, command):
                self.commands.append(command)

            def sendcontrol(self, _char):
                pass

            def expect(self, _pattern, timeout=None):
                del timeout
                raise pexpect.TIMEOUT("stop after command capture")

        class FakeRunner:
            def expect_prompt(self, _child, _timeout=None):
                pass

        cfg = {
            "SAMPLES_PER_TRIAL": "1", "WARMUP_SAMPLES": "0",
            "EVENT_TIMEOUT": "1", "_CLOCK_OFFSET_NS": "0",
        }
        captured = []
        captured.extend([
            build_preflight_command("find /usr", "W2_PREFLIGHT_TEST_RC:"),
            build_output_load_command("find /usr", "W2_OUTPUT_EVENT_", 0.1),
        ])

        clock_child = FakeChild()
        with self.assertRaises(RuntimeError):
            estimate_clock_offset(clock_child, FakeRunner(), 1, 1, 1)
        captured.extend(clock_child.commands)

        for command in captured:
            result = subprocess.run(["bash", "-n", "-c", command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, msg=f"{command}\n{result.stderr}")

    def test_collect_events_has_overall_deadline(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        child = FakeChild([])
        cfg = {
            "WARMUP_SAMPLES": "0",
            "SAMPLES_PER_TRIAL": "1",
            "EVENT_TIMEOUT": "1",
            "EVENT_TOTAL_TIMEOUT": "0",
            "_CLOCK_OFFSET_NS": "0",
        }
        with self.assertRaises(pexpect.TIMEOUT):
            collect_events(child, pattern, cfg, lambda *_args: True)
        self.assertEqual(child.read_sizes, [])

    def test_clock_invalid_marker_is_not_replaced(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        child = FakeChild([
            "W2_OUTPUT_EVENT_1:1234567890123456789\n",
            "W2_OUTPUT_EVENT_2:1234567890123456790\n",
        ])
        indexes = []
        cfg = {
            "WARMUP_SAMPLES": "0",
            "SAMPLES_PER_TRIAL": "1",
            "EVENT_TIMEOUT": "1",
            "EVENT_TOTAL_TIMEOUT": "5",
            "_CLOCK_OFFSET_NS": "0",
        }

        def reject_marker(index, *_args):
            indexes.append(index)
            return False

        collect_events(child, pattern, cfg, reject_marker)
        self.assertEqual(indexes, [1])
        self.assertEqual(len(child.read_sizes), 1)


if __name__ == "__main__":
    unittest.main()
